"""Octopus channel lists: keys, frequency lookup, matching channel specs, and diffs between lists."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sat_to_ip.octopus.client import OctopusChannel

logger = logging.getLogger(__name__)

# Spec keywords → the Octopus "type" they select (None = every type)
KEYWORDS: dict[str, str | None] = {"all": None, "tv": "video", "video": "video", "radio": "audio", "audio": "audio"}


def ch_freq(ch: OctopusChannel) -> int | float | str | None:
    return ch.get("frequency") or ch.get("freq")


def channel_key(ch: OctopusChannel) -> str:
    """The stable Channel.key / ChannelRef.key for an Octopus channel."""
    sid = ch.get("serviceid")
    if sid is not None and sid != "":
        return f"octopus:{sid}"
    return f"octopus:{ch.get('name', '')}@{ch_freq(ch)}"


@dataclass
class ScanDiff:
    added: list[OctopusChannel] = field(default_factory=list)
    removed: list[OctopusChannel] = field(default_factory=list)
    freq_changed: list[tuple[OctopusChannel, OctopusChannel]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.freq_changed)


def compute_scan_diff(before: list[OctopusChannel], after: list[OctopusChannel]) -> ScanDiff:
    """Return a ScanDiff comparing two channel lists by serviceid."""
    before_by_id = {ch["serviceid"]: ch for ch in before if "serviceid" in ch}
    after_by_id = {ch["serviceid"]: ch for ch in after if "serviceid" in ch}

    added = [ch for sid, ch in after_by_id.items() if sid not in before_by_id]
    removed = [ch for sid, ch in before_by_id.items() if sid not in after_by_id]
    freq_changed = [
        (before_by_id[sid], ch)
        for sid, ch in after_by_id.items()
        if sid in before_by_id and ch_freq(before_by_id[sid]) != ch_freq(ch)
    ]
    return ScanDiff(added=added, removed=removed, freq_changed=freq_changed)


def log_scan_diff(before: list[OctopusChannel], after: list[OctopusChannel]) -> None:
    """Log channels added, removed, or changed frequency between two scans."""
    diff = compute_scan_diff(before, after)
    if not diff.has_changes:
        logger.info("   No changes detected vs previous scan")
        return
    if diff.added:
        logger.info(f"   New channels ({len(diff.added)}):")
        for ch in diff.added:
            logger.info(f"     + {ch.get('name', '?')}  freq={ch_freq(ch)}  type={ch.get('type', '?')}")
    if diff.removed:
        logger.warning(f"   ⚠️  Channels no longer found ({len(diff.removed)}):")
        for ch in diff.removed:
            logger.warning(f"     - {ch.get('name', '?')}  freq={ch_freq(ch)}")
    if diff.freq_changed:
        logger.warning(f"   ⚠️  Channels with changed frequency ({len(diff.freq_changed)}):")
        for ch_before, ch_after in diff.freq_changed:
            logger.warning(f"     ~ {ch_after.get('name', '?')}  {ch_freq(ch_before)} → {ch_freq(ch_after)}")


def match_entry(entry: str, channels: list[OctopusChannel], taken: set[str]) -> list[OctopusChannel]:
    """Return channels matching one 'Name' or 'Name@Frequency' entry, skipping keys already taken."""
    if "@" in entry:
        name_part, freq_part = entry.split("@", 1)
        name_lower, freq = name_part.strip().lower(), freq_part.strip()
        return [
            ch
            for ch in channels
            if ch.get("name", "").lower() == name_lower
            and str(ch_freq(ch) or "") == freq
            and channel_key(ch) not in taken
        ]
    # No frequency — first match only; use Name@Frequency to be explicit
    name_lower = entry.strip().lower()
    for ch in channels:
        if ch.get("name", "").lower() == name_lower and channel_key(ch) not in taken:
            return [ch]
    return []


def filter_channels(channels: list[OctopusChannel], specs: Sequence[str]) -> tuple[list[OctopusChannel], list[str]]:
    """Return (matched_channels in spec order, unmatched specs). No channel is matched twice.

    Each spec is 'all', 'tv' (or 'video'), 'radio' (or 'audio'), 'Name' or 'Name@Frequency'.
    """
    matches: list[OctopusChannel] = []
    unmatched: list[str] = []
    taken: set[str] = set()
    for spec in specs:
        if spec in KEYWORDS:
            kind = KEYWORDS[spec]
            found = [ch for ch in channels if kind in (None, ch.get("type")) and channel_key(ch) not in taken]
        else:
            found = match_entry(spec, channels, taken)
            if not found:
                unmatched.append(spec)
        matches.extend(found)
        taken.update(channel_key(ch) for ch in found)
    return matches, unmatched
