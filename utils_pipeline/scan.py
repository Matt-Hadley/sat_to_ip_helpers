"""Scan diff helpers — compare channel lists across Octopus scans."""
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


def restore_dms_after_scan(
    pre_dms: list[dict], post_available: list[dict]
) -> tuple[list[dict], list[str]]:
    """Match pre-scan DMS channels to post-scan available channels by serviceid.

    Returns (restored_channels, missing_names).
    Restored channels use post-scan objects (updated parameters) with original positions.
    """
    post_by_id = {ch["serviceid"]: ch for ch in post_available if "serviceid" in ch}
    restored, missing = [], []
    for ch in sorted(pre_dms, key=lambda c: c.get("position", 0)):
        sid = ch.get("serviceid")
        if sid and sid in post_by_id:
            post_ch = dict(post_by_id[sid])
            post_ch["position"] = ch.get("position", 0)
            restored.append(post_ch)
        else:
            missing.append(ch.get("name", sid))
    return restored, missing
