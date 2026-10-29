"""Octopus NET channel records: keys, frequency lookup, and the diff between two scans."""

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from sat_to_ip.octopus_net.client import OctopusChannel

logger = logging.getLogger(__name__)


def ch_freq(ch: OctopusChannel) -> int | float | str | None:
    return ch.get("frequency") or ch.get("freq")


def mhz(value: Any) -> float | None:
    """A frequency in MHz, or None when the record has no usable one."""
    try:
        freq = float(value)
    except (TypeError, ValueError):
        return None
    return freq if math.isfinite(freq) else None


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

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed)


def compute_scan_diff(before: list[OctopusChannel], after: list[OctopusChannel]) -> ScanDiff:
    """Return a ScanDiff comparing two channel lists by serviceid.

    The Octopus NET's serviceid includes the frequency, so a moved channel shows as removed + added
    (refresh's retune step logs the move itself).
    """
    before_by_id = {ch["serviceid"]: ch for ch in before if "serviceid" in ch}
    after_by_id = {ch["serviceid"]: ch for ch in after if "serviceid" in ch}

    added = [ch for sid, ch in after_by_id.items() if sid not in before_by_id]
    removed = [ch for sid, ch in before_by_id.items() if sid not in after_by_id]
    return ScanDiff(added=added, removed=removed)


def log_scan_diff(before: list[OctopusChannel], after: list[OctopusChannel]) -> None:
    """Log channels added or removed between two scans."""
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
