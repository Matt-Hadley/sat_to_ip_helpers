"""Write the playlists and guide that file-reading DVRs (Jellyfin, Threadfin) pick up from disk.

Attributes are the ones both actually read: tvg-id (matched to the XMLTV <channel id>), tvg-name,
tvg-chno (channel number) and group-title. Neither has a radio marker in M3U: Jellyfin's M3U
tuner makes every channel TV, so radio goes in its own playlist and "Radio" group instead.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from sat_to_ip import m3u
from sat_to_ip.exceptions import CheckError
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


def write_lineup_files(
    output_dir: Path, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool, number_in_label: bool = False
) -> FileOutput:
    """Write tv.m3u and radio.m3u (always both, so a tuner never points at a missing file), and
    guide.xml when the guide has XMLTV. With a guide, only matched channels are written.

    number_in_label: label entries "<number> <name>". Jellyfin reads a label starting with a number
    and a space as "number name", so without this "5 USA" would show as "USA".
    """
    if guide is not None:
        for kind in PLAYLISTS:
            channels = lineup.of_kind(kind)
            if channels and not any(ch.key in guide.refs for ch in channels):
                raise CheckError(
                    f"Guide {guide.source!r} matched none of the {len(channels)} {kind} channels; "
                    f"refusing to empty {PLAYLISTS[kind][0]}."
                )
    directory = Path(tempfile.mkdtemp(prefix="sat-to-ip-")) if dry_run else output_dir
    paths: list[Path] = []
    if guide is not None and guide.xmltv is not None:
        # Guide first, so a reader that sees the new playlist also finds its channels in the guide.
        paths.append(directory / GUIDE_FILE)
        write_atomic(paths[-1], guide.xmltv)
    counts: dict[Kind, int] = {}
    for kind, (filename, group) in PLAYLISTS.items():
        entries = [
            _entry(ch, guide.refs.get(ch.key) if guide else None, group, number_in_label)
            for ch in lineup.of_kind(kind)
            if guide is None or ch.key in guide.refs
        ]
        counts[kind] = len(entries)
        paths.append(directory / filename)
        write_atomic(paths[-1], m3u.render(entries))
    return FileOutput(directory, tuple(paths), counts)


def _entry(ch: Channel, ref: GuideRef | None, group: str, number_in_label: bool) -> m3u.Entry:
    attrs = [("tvg-id", ref.guide_id)] if ref else []
    attrs += [
        ("tvg-name", (ref.display_name if ref else None) or ch.name),
        ("tvg-chno", str(ch.number)),
        ("group-title", group),
    ]
    label = f"{ch.number} {ch.name}" if number_in_label else ch.name
    return m3u.Entry(label=label, url=ch.stream_url, attrs=tuple(attrs))
