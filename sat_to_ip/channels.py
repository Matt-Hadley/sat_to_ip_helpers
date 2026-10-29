"""Octopus channel lists: frequency lookup, filtering by name / name@frequency, and diffs between lists."""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def ch_freq(ch: dict):
    return ch.get("frequency") or ch.get("freq")


@dataclass
class ScanDiff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    freq_changed: list[tuple[dict, dict]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.freq_changed)


def compute_scan_diff(before: list[dict], after: list[dict]) -> ScanDiff:
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


def log_scan_diff(before: list[dict], after: list[dict]) -> None:
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


def match_entry(entry: str, channels: list[dict], seen_ids: set) -> list[dict]:
    """Return channels matching a single spec entry ('Name' or 'Name@Frequency')."""
    if "@" in entry:
        name_part, freq_part = entry.split("@", 1)
        name_lower, freq = name_part.strip().lower(), freq_part.strip()
        return [
            ch
            for ch in channels
            if ch.get("name", "").lower() == name_lower
            and str(ch_freq(ch) or "") == freq
            and ch.get("serviceid", id(ch)) not in seen_ids
        ]
    # No frequency — first match only; use Name@Frequency to be explicit
    name_lower = entry.lower()
    for ch in channels:
        if ch.get("name", "").lower() == name_lower and ch.get("serviceid", id(ch)) not in seen_ids:
            return [ch]
    return []


def filter_channels(channels: list[dict], spec: str) -> tuple[list[dict], list[str]]:
    """Return (matched_channels, unmatched_spec_entries).

    spec can be: all | video | audio | comma-separated 'Name' or 'Name@Frequency'
    """
    if spec == "all":
        return channels, []
    if spec in ("video", "audio"):
        return [ch for ch in channels if ch.get("type") == spec], []
    matches: list[dict] = []
    unmatched: list[str] = []
    seen_ids: set = set()
    for entry in (e.strip() for e in spec.split(",")):
        found = match_entry(entry, channels, seen_ids)
        if found:
            matches.extend(found)
            seen_ids.update(ch.get("serviceid", id(ch)) for ch in found)
        else:
            unmatched.append(entry)
    return matches, unmatched
