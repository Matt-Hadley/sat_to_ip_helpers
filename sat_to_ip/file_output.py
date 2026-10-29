"""Write the playlists and guide that file-reading DVRs (Jellyfin, Threadfin) pick up from disk.

Attributes are the ones both actually read: tvg-id (matched to the XMLTV <channel id>), tvg-name,
tvg-chno (channel number) and group-title. Neither has a radio marker in M3U: Jellyfin's M3U
tuner makes every channel TV, so radio goes in its own playlist and "Radio" group instead.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from sat_to_ip import m3u
from sat_to_ip.files import write_atomic
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Kind, Lineup

PLAYLISTS: dict[Kind, tuple[str, str]] = {"tv": ("tv.m3u", "TV"), "radio": ("radio.m3u", "Radio")}
GUIDE_FILE = "guide.xml"


@dataclass(frozen=True)
class FileOutput:
    directory: Path  # output_dir, or the temp dir a dry run wrote to instead
    paths: tuple[Path, ...]
    counts: dict[Kind, int]

    @property
    def channels(self) -> int:
        return sum(self.counts.values())


def write_lineup_files(output_dir: Path, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> FileOutput:
    """Write tv.m3u and radio.m3u (always both, so a tuner never points at a missing file), and
    guide.xml when the guide has XMLTV. With a guide, only matched channels are written."""
    directory = Path(tempfile.mkdtemp(prefix="sat-to-ip-")) if dry_run else output_dir
    paths: list[Path] = []
    if guide is not None and guide.xmltv is not None:
        # Guide first, so a reader that sees the new playlist also finds its channels in the guide.
        paths.append(directory / GUIDE_FILE)
        write_atomic(paths[-1], guide.xmltv)
    counts: dict[Kind, int] = {}
    for kind, (filename, group) in PLAYLISTS.items():
        entries = [
            _entry(ch, guide.refs.get(ch.key) if guide else None, group)
            for ch in lineup.of_kind(kind)
            if guide is None or ch.key in guide.refs
        ]
        counts[kind] = len(entries)
        paths.append(directory / filename)
        write_atomic(paths[-1], m3u.render(entries))
    return FileOutput(directory, tuple(paths), counts)


def _entry(ch: Channel, ref: GuideRef | None, group: str) -> m3u.Entry:
    attrs = [("tvg-id", ref.guide_id)] if ref else []
    attrs += [
        ("tvg-name", (ref.display_name if ref else None) or ch.name),
        ("tvg-chno", str(ch.number)),
        ("group-title", group),
    ]
    return m3u.Entry(label=ch.name, url=ch.stream_url, attrs=tuple(attrs))
