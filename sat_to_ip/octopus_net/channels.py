"""Octopus NET channel records: keys, frequency lookup, and the diff between two scans."""

import logging
from dataclasses import dataclass, field
from typing import Any

from sat_to_ip.octopus_net.client import OctopusChannel

logger = logging.getLogger(__name__)


def ch_freq(ch: OctopusChannel) -> int | float | str | None:
    return ch.get("frequency") or ch.get("freq")


def mhz(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def channel_key(ch: OctopusChannel) -> str:
    """The Channel.key / ChannelRef.key for an Octopus NET channel: service ID and frequency.

    Service IDs are only unique per transponder, so the frequency (whole MHz, however the record writes it)
    tells apart two channels that share one.
    """
    sid = ch.get("serviceid")
    if sid is None or sid == "":
        return f"octopus_net:{ch.get('name', '')}@{ch_freq(ch)}"
    freq = mhz(ch_freq(ch))
    return f"octopus_net:{sid}" + (f"@{round(freq)}" if freq is not None else "")


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
