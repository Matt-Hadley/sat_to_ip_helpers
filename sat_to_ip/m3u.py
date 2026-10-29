"""Parse and render M3U playlists (#EXTINF entries with key="value" attributes)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


@dataclass(frozen=True)
class Entry:
    label: str
    url: str
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str) -> str | None:
        return next((v for k, v in self.attrs if k == key), None)


def parse(text: str) -> list[Entry]:
    """Return every #EXTINF entry; its URL is the next line that isn't a comment."""
    lines = [line.strip() for line in text.splitlines()]
    entries = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        header, _, label = line.partition(",")
        url = next((nxt for nxt in lines[i + 1 :] if nxt and not nxt.startswith("#")), "")
        entries.append(Entry(label=label.strip(), url=url, attrs=tuple(_ATTR.findall(header))))
    return entries


def render(entries: Iterable[Entry]) -> str:
    """Render entries as an M3U playlist; values lose any double quotes (M3U has no escaping)."""
    out = ["#EXTM3U"]
    for e in entries:
        attrs = "".join(f' {k}="{v.replace(chr(34), chr(39))}"' for k, v in e.attrs)
        out += [f"#EXTINF:-1{attrs},{e.label}", e.url]
    return "\n".join(out) + "\n"
