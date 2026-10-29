"""Write the playlists and guide that file-reading DVRs (Jellyfin, Threadfin) pick up from disk.

Attributes are the ones both actually read: tvg-id (matched to the XMLTV <channel id>), tvg-name,
tvg-chno (channel number) and group-title. Neither has a radio marker in M3U: Jellyfin's M3U
tuner makes every channel TV, so radio goes in its own playlist and "Radio" group instead.
"""

from dataclasses import dataclass
from pathlib import Path

from sat_to_ip import m3u
from sat_to_ip.checks import publishable
from sat_to_ip.files import write_atomic
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Kind, Lineup

PLAYLISTS: dict[Kind, tuple[str, str]] = {"tv": ("tv.m3u", "TV"), "radio": ("radio.m3u", "Radio")}
GUIDE_FILE = "guide.xml"


@dataclass(frozen=True)
class FileOutput:
    directory: Path
    counts: dict[Kind, int]

    @property
    def channels(self) -> int:
        return sum(self.counts.values())


def write_lineup_files(
    output_dir: Path, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool, number_in_label: bool = False
) -> FileOutput:
    """Write tv.m3u and radio.m3u (always both, so a tuner never points at a missing file), and
    guide.xml when the guide has XMLTV. With a guide, only matched channels are written.

    Everything is built and checked before anything is written; a dry run writes nothing.
    number_in_label: label entries "<number> <name>". Jellyfin reads a label starting with a number
    and a space as "number name", so without this "5 USA" would show as "USA".
    """
    playlists = {
        filename: [
            _entry(ch, guide.refs.get(ch.key) if guide else None, group, number_in_label)
            for ch in publishable(lineup, guide, kind, f"{output_dir / filename}")
        ]
        for kind, (filename, group) in PLAYLISTS.items()
    }
    counts: dict[Kind, int] = {kind: len(playlists[filename]) for kind, (filename, _) in PLAYLISTS.items()}
    if dry_run:
        return FileOutput(output_dir, counts)
    if guide is not None and guide.xmltv is not None:
        # Guide first, so a reader that sees the new playlist also finds its channels in the guide.
        write_atomic(output_dir / GUIDE_FILE, guide.xmltv)
    for filename, entries in playlists.items():
        write_atomic(output_dir / filename, m3u.render(entries))
    return FileOutput(output_dir, counts)


def _entry(ch: Channel, ref: GuideRef | None, group: str, number_in_label: bool) -> m3u.Entry:
    attrs = [("tvg-id", ref.guide_id)] if ref else []
    attrs += [
        ("tvg-name", (ref.display_name if ref else None) or ch.name),
        ("tvg-chno", str(ch.number)),
        ("group-title", group),
    ]
    label = f"{ch.number} {ch.name}" if number_in_label else ch.name
    return m3u.Entry(label=label, url=ch.stream_url, attrs=tuple(attrs))
