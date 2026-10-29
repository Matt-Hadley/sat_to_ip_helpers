"""XMLTV guide source: fetch a feed, match channels by name, filter the feed to the matches.

The feed is untrusted. It is parsed with the stdlib expat parser, which never fetches external
entities and (expat >= 2.4.1, bundled with Python 3.11+) refuses billion-laughs style expansion.
"""

import gzip
import io
import logging
import os
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

import requests
import urllib3

from sat_to_ip import http
from sat_to_ip.config import Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.mappings import load_mappings
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Lineup
from sat_to_ip.names import normalize_name

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50_000_000  # the Freeview-EPG feed is ~21 MB
DOWNLOAD_DEADLINE = 300  # seconds for the whole download: per-read timeouts alone let a trickle run forever
_ID_SUFFIX = re.compile(r"\.[a-z]{2}$", re.IGNORECASE)  # "BBCOne.uk" -> "BBCOne"


@dataclass
class XmltvGuide:
    name: str
    url: str | None = None
    path: Path | None = None
    mappings: Mapping[str, str] = field(default_factory=dict)  # lowercased channel name -> xmltv id
    max_bytes: int = DEFAULT_MAX_BYTES
    kind: str = "xmltv"

    def match(self, lineup: Lineup) -> GuideMatch:
        out = io.BytesIO()  # the filtered feed, written as it is parsed so matched elements never pile up
        channels: dict[str, ET.Element] = {}
        programmes = 0
        matched: tuple[dict[str, GuideRef], set[str]] | None = None  # set once every <channel> is read
        with self._open() as raw:
            elements = _top_level(self.name, _feed(self.name, raw, self.max_bytes))
            root = next(elements)
            for elem in elements:
                if elem.tag == "channel" and elem.get("id"):
                    if matched is None:
                        channels.setdefault(elem.get("id", ""), elem)
                    else:
                        logger.warning(f"{self.name}: ignoring <channel id={elem.get('id')!r}> after programmes")
                elif elem.tag == "programme":
                    if matched is None:  # XMLTV puts every <channel> before the first <programme>
                        matched = self._start(out, root, lineup, channels)
                    if elem.get("channel") in matched[1]:
                        ET.ElementTree(elem).write(out, encoding="utf-8", xml_declaration=False)
                        programmes += 1
        refs, _ = matched or self._start(out, root, lineup, channels)
        out.write(b"</tv>")
        unmatched = tuple(ch for ch in lineup if ch.key not in refs)
        logger.info(f"{self.name}: matched {len(refs)}/{len(lineup)} channels, {programmes} programmes")
        return GuideMatch(self.name, self.kind, refs, unmatched, out.getvalue())

    def _start(
        self, out: io.BytesIO, root: ET.Element, lineup: Lineup, channels: Mapping[str, ET.Element]
    ) -> tuple[dict[str, GuideRef], set[str]]:
        """Match the lineup, then write the declaration, the <tv> start tag and the matched channels."""
        refs, keep = self._match(lineup, channels)
        tv = ET.Element(root.tag, root.attrib)
        tv.text = root.text
        head = ET.tostring(tv, encoding="UTF-8", xml_declaration=True, short_empty_elements=False)
        out.write(head.removesuffix(b"</tv>"))
        for cid, elem in channels.items():
            if cid in keep:
                ET.ElementTree(elem).write(out, encoding="utf-8", xml_declaration=False)
        return refs, keep

    def _match(self, lineup: Lineup, channels: Mapping[str, ET.Element]) -> tuple[dict[str, GuideRef], set[str]]:
        names = {cid: [d.text or "" for d in el.findall("display-name")] for cid, el in channels.items()}
        by_name = _unique_index((normalize_name(n), cid) for cid, ns in names.items() for n in ns)
        by_id = _unique_index((normalize_name(_ID_SUFFIX.sub("", cid)), cid) for cid in names)
        refs: dict[str, GuideRef] = {}
        for ch in lineup:
            cid = _pick(ch, self.mappings, by_name, by_id)
            if cid in names:
                refs[ch.key] = GuideRef(cid, next(iter(names[cid]), "") or None)
            elif cid:
                logger.warning(f"{self.name}: {ch.name!r} is mapped to {cid!r}, which the feed doesn't have")
        return refs, {ref.guide_id for ref in refs.values()}

    def _open(self) -> io.BufferedIOBase:
        if self.path is not None:
            try:
                f = open(self.path, "rb")
                size = os.fstat(f.fileno()).st_size
            except OSError as exc:
                raise IntegrationError(f"{self.name}: can't read {self.path}: {exc}") from exc
            if size > self.max_bytes:
                f.close()
                raise IntegrationError(_too_large(self.name, self.max_bytes))
            return f
        assert self.url is not None
        deadline = monotonic() + DOWNLOAD_DEADLINE
        try:
            resp = http.session().get(self.url, stream=True)
            resp.raise_for_status()
            buf = io.BytesIO()
            # read1 returns what one socket read gives, so a trickling server can't stall a deadline check
            while chunk := resp.raw.read1(1 << 20, decode_content=True):
                buf.write(chunk)
                if buf.tell() > self.max_bytes:
                    raise IntegrationError(_too_large(self.name, self.max_bytes))
                if monotonic() > deadline:
                    raise IntegrationError(f"{self.name}: downloading {self.url} took over {DOWNLOAD_DEADLINE}s")
            buf.seek(0)
            return buf
        except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
            raise IntegrationError(f"{self.name}: can't download {self.url}: {exc}") from exc


class _Capped:
    """A read-only stream that fails cleanly past max_bytes (huge files, gzip bombs) or on a read error."""

    def __init__(self, name: str, f: io.BufferedIOBase, max_bytes: int, error: str):
        self.name, self.f, self.max_bytes, self.error = name, f, max_bytes, error
        self.left = max_bytes

    def read(self, size: int = -1) -> bytes:
        try:  # never read more than one byte past the cap
            data = self.f.read(self.left + 1 if size < 0 else min(size, self.left + 1))
        except (OSError, EOFError, zlib.error) as exc:
            raise IntegrationError(f"{self.name}: {self.error}: {exc}") from exc
        self.left -= len(data)
        if self.left < 0:
            raise IntegrationError(_too_large(self.name, self.max_bytes))
        return data


def _feed(name: str, raw: io.BufferedIOBase, max_bytes: int) -> _Capped:
    """The feed as a capped stream, decompressed as it is read if it is gzip (detected by magic bytes)."""
    gzipped = raw.read(2) == b"\x1f\x8b"
    raw.seek(0)
    if gzipped:
        return _Capped(name, gzip.GzipFile(mode="rb", fileobj=raw), max_bytes, "corrupt gzip feed")
    return _Capped(name, raw, max_bytes, "can't read feed")


def _too_large(name: str, max_bytes: int) -> str:
    return f"{name}: feed is larger than max_bytes ({max_bytes} bytes)"


def _top_level(name: str, feed: _Capped) -> Iterator[ET.Element]:
    """Yield the <tv> root as it opens, then each child once its tail is parsed, detached so the tree never grows."""
    root: ET.Element | None = None
    done: ET.Element | None = None  # a closed child, held until the next event so its tail (whitespace) is set
    depth = 0
    try:
        for event, elem in ET.iterparse(feed, events=("start", "end")):
            if done is not None:
                yield done
                done = None
            if event == "start":
                depth += 1
                if root is None:
                    if elem.tag != "tv":
                        raise IntegrationError(f"{name}: not an XMLTV feed (root is <{elem.tag}>)")
                    root = elem
                    yield root
            else:
                depth -= 1
                if depth == 1 and root is not None:
                    root.remove(elem)
                    done = elem
    except ET.ParseError as exc:
        raise IntegrationError(f"{name}: malformed XMLTV feed: {exc}") from exc


def _pick(ch: Channel, mappings: Mapping[str, str], by_name: Mapping[str, str], by_id: Mapping[str, str]) -> str | None:
    """Explicit mapping first, then display-name, then id without its country suffix."""
    if (mapped := mappings.get(ch.name.strip().lower())) is not None:
        return mapped
    key = normalize_name(ch.name)
    return by_name.get(key) or by_id.get(key)


def _unique_index(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """key -> id, leaving out keys shared by several ids (e.g. "Challenge.uk" and "Challenge.ie")."""
    seen: dict[str, set[str]] = {}
    for key, cid in pairs:
        if key:
            seen.setdefault(key, set()).add(cid)
    return {key: next(iter(ids)) for key, ids in seen.items() if len(ids) == 1}


def from_config(section: Section) -> XmltvGuide:
    url = section.get_url("url") if section.get_optional_str("url") else None
    path = section.get_optional_path("path")
    if (url is None) == (path is None):
        raise section.error("url", "set exactly one of url (to download the feed) or path (a local file)")
    extra = section.get_str_map("extra_mappings")
    try:
        mappings = load_mappings("xmltv", section.get_optional_str("mappings"), extra)
    except ConfigError as exc:
        raise section.error("mappings", str(exc)) from exc
    max_bytes = section.get_int("max_bytes", DEFAULT_MAX_BYTES, minimum=1)
    return XmltvGuide(section.name, url, path, mappings, max_bytes)
