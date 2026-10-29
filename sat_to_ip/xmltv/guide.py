"""XMLTV guide source: fetch a feed, match channels by name, filter the feed to the matches.

The feed is untrusted. It is parsed with the stdlib expat parser, which never fetches external
entities and (expat >= 2.4.1, bundled with Python 3.11+) refuses billion-laughs style expansion.
"""

import gzip
import io
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import requests

from sat_to_ip import http
from sat_to_ip.config import Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.mappings import load_mappings
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Lineup
from sat_to_ip.names import normalize_name

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 300_000_000
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
        elements = _top_level(self.name, _decompress(self.name, self._load(), self.max_bytes))
        root = next(elements)
        channels: dict[str, ET.Element] = {}
        programmes: list[ET.Element] = []
        matched: tuple[dict[str, GuideRef], set[str]] | None = None  # set once every <channel> is read
        for elem in elements:
            if elem.tag == "channel" and elem.get("id"):
                if matched is None:
                    channels.setdefault(elem.get("id", ""), elem)
                else:
                    logger.warning(f"{self.name}: ignoring <channel id={elem.get('id')!r}> after programmes")
            elif elem.tag == "programme":
                if matched is None:  # XMLTV puts every <channel> before the first <programme>
                    matched = self._match(lineup, channels)
                if elem.get("channel") in matched[1]:
                    programmes.append(elem)
        refs, keep = matched or self._match(lineup, channels)
        root[:] = [elem for cid, elem in channels.items() if cid in keep] + programmes
        unmatched = tuple(ch for ch in lineup if ch.key not in refs)
        logger.info(f"{self.name}: matched {len(refs)}/{len(lineup)} channels, {len(programmes)} programmes")
        xmltv = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        return GuideMatch(self.name, self.kind, refs, unmatched, xmltv)

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

    def _load(self) -> bytes:
        if self.path is not None:
            try:
                with open(self.path, "rb") as f:
                    return _capped(self.name, f.read(self.max_bytes + 1), self.max_bytes)
            except OSError as exc:
                raise IntegrationError(f"{self.name}: can't read {self.path}: {exc}") from exc
        assert self.url is not None
        try:
            resp = http.session().get(self.url, stream=True)
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=1 << 20):
                buf += chunk
                _capped(self.name, buf, self.max_bytes)
            return bytes(buf)
        except requests.RequestException as exc:
            raise IntegrationError(f"{self.name}: can't download {self.url}: {exc}") from exc


def _top_level(name: str, data: bytes) -> Iterator[ET.Element]:
    """Yield the <tv> root as it opens, then each child as it closes, detached so the tree never grows."""
    root: ET.Element | None = None
    depth = 0
    try:
        for event, elem in ET.iterparse(io.BytesIO(data), events=("start", "end")):
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
                    yield elem
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


def _capped(name: str, data: bytes | bytearray, max_bytes: int) -> bytes:
    if len(data) > max_bytes:
        raise IntegrationError(f"{name}: feed is larger than max_bytes ({max_bytes} bytes)")
    return bytes(data)


def _decompress(name: str, data: bytes, max_bytes: int) -> bytes:
    if not data.startswith(b"\x1f\x8b"):
        return data
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            return _capped(name, f.read(max_bytes + 1), max_bytes)
    except (OSError, EOFError) as exc:
        raise IntegrationError(f"{name}: corrupt gzip feed: {exc}") from exc


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
