"""Parse and render M3U playlists (#EXTINF entries with key="value" attributes)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")  # str.splitlines() breaks on all of these


def _clean(value: str) -> str:
    """One line, no double quotes: names come from untrusted guide feeds, and a newline would start a new entry."""
    return _CONTROL.sub(" ", value).replace('"', "'").strip()


@dataclass(frozen=True)
class Entry:
    label: str
    url: str
    attrs: tuple[tuple[str, str], ...] = ()


def _split_extinf(line: str) -> tuple[str, str]:
    """Split '#EXTINF:-1 a="x, y",Label' at the first comma outside quotes: (header, label)."""
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            return line[:i], line[i + 1 :]
    return line, ""


def parse(text: str) -> list[Entry]:
    """Return every #EXTINF entry. Its URL is the next line that isn't a comment, before the next #EXTINF
    (an entry without one gets an empty URL rather than the next entry's)."""
    lines = [line.strip() for line in text.splitlines()]
    entries = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        header, label = _split_extinf(line)
        url = ""
        for nxt in lines[i + 1 :]:
            if nxt.startswith("#EXTINF:"):
                break
            if nxt and not nxt.startswith("#"):
                url = nxt
                break
        entries.append(Entry(label=label.strip(), url=url, attrs=tuple(_ATTR.findall(header))))
    return entries


def render(entries: Iterable[Entry]) -> str:
    """Render entries as an M3U playlist. M3U has no escaping, so values are cleaned instead."""
    out = ["#EXTM3U"]
    for e in entries:
        attrs = "".join(f' {k}="{_clean(v)}"' for k, v in e.attrs)
        out += [f"#EXTINF:-1{attrs},{_CONTROL.sub(' ', e.label).strip()}", _CONTROL.sub("", e.url)]
    return "\n".join(out) + "\n"
