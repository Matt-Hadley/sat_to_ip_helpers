"""Safety checks: stop an unattended run before a bad scan or feed can gut a lineup."""

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sat_to_ip.exceptions import CheckError
from sat_to_ip.model import Channel, ChannelRef, GuideMatch, Kind, Lineup, SelectionPlan

logger = logging.getLogger(__name__)

# `extra` for a warning a run can't fix by itself: the CLI then exits 1 so an unattended run reports it
NEEDS_ATTENTION = {"needs_attention": True}


@dataclass(frozen=True)
class CheckSettings:
    max_removed: int = 5
    min_guide_match: float = 0.5


def check_removals(plan: SelectionPlan, max_removed: int, *, allow: bool) -> None:
    """Refuse to save a selection that drops more than max_removed of the receiver's saved channels."""
    removed = plan.removed
    if len(removed) > max_removed and not allow:
        names = ", ".join(ch.name for ch in removed[:10]) + (" …" if len(removed) > 10 else "")
        raise CheckError(
            f"Refusing to remove {len(removed)} saved channels (limit {max_removed}): {names}. "
            "Check the scan, or re-run with --allow-removals if this is intended."
        )


def check_selection_not_empty(plan: SelectionPlan) -> None:
    """Never replace a saved channel list with nothing, whatever the flags: no channel matched."""
    if plan.current and not plan.proposed:
        raise CheckError(
            f"The selection matches no channels; refusing to replace the {len(plan.current)} saved ones with nothing. "
            "Check the [[channels]] blocks in your config."
        )


def check_lineup(lineup: Lineup) -> None:
    """Refuse a lineup that's empty, or that a receiver built wrongly (repeated keys, an unknown kind)."""
    if not lineup.channels:
        raise CheckError("The receiver has no saved channels; refusing to publish an empty lineup.")
    repeated = sorted(key for key, n in Counter(ch.key for ch in lineup).items() if n > 1)
    if repeated:
        raise CheckError(f"The receiver gave several channels the same key: {', '.join(repeated)}")
    odd = sorted({repr(ch.kind) for ch in lineup if ch.kind not in ("tv", "radio")})
    if odd:
        raise CheckError(f"The receiver gave channels an unknown kind {', '.join(odd)} (must be 'tv' or 'radio')")


def check_lineup_complete(saved: Sequence[ChannelRef], lineup: Lineup, max_removed: int, *, allow: bool) -> None:
    """Refuse to publish when many of the receiver's saved channels are missing from its lineup.

    A receiver that answers with a short stream list (e.g. just after a reboot) would otherwise replace
    every DVR's lineup with the few channels it did return.
    """
    have = {ch.key for ch in lineup}
    missing = [ch for ch in saved if ch.key not in have]
    for ch in missing:
        logger.warning(f"   {ch.name!r} is saved on the receiver but it has no stream for it", extra=NEEDS_ATTENTION)
    if len(missing) > max_removed and not allow:
        names = ", ".join(ch.name for ch in missing[:10]) + (" …" if len(missing) > 10 else "")
        raise CheckError(
            f"{len(missing)} saved channels have no stream on the receiver (limit {max_removed}): {names}. "
            "Check the receiver, or re-run with --allow-removals if this is intended."
        )


def publishable(lineup: Lineup, guide: GuideMatch | None, kind: Kind, target: str) -> tuple[Channel, ...]:
    """The channels of `kind` a target should get: only guide-matched ones when it uses a guide.

    Refuses (CheckError) to hand a target an empty list when the lineup has channels of that kind:
    that would wipe the target's TV or radio channels. Every publisher goes through this.
    """
    channels = lineup.of_kind(kind)
    if guide is None:
        return channels
    matched = tuple(ch for ch in channels if ch.key in guide.refs)
    if channels and not matched:
        raise CheckError(
            f"Guide {guide.source!r} matched none of the {len(channels)} {kind} channels; refusing to empty {target}."
        )
    return matched


def check_guide_match(match: GuideMatch, minimum: float) -> None:
    """Refuse a guide that matched fewer than `minimum` of the channels (a broken or changed feed)."""
    if match.total and match.match_ratio < minimum:
        raise CheckError(
            f"Guide {match.source!r} matched only {len(match.refs)}/{match.total} channels "
            f"(minimum {minimum:.0%}). Check the feed or the mappings."
        )
