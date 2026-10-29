"""Safety checks: stop an unattended run before a bad scan or feed can gut a lineup."""

import logging
from dataclasses import dataclass

from sat_to_ip.exceptions import CheckError
from sat_to_ip.model import GuideMatch, Lineup, SelectionPlan

logger = logging.getLogger(__name__)


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


def check_lineup_not_empty(lineup: Lineup) -> None:
    if not lineup.channels:
        raise CheckError("The receiver has no saved channels; refusing to publish an empty lineup.")


def check_guide_match(match: GuideMatch, minimum: float) -> None:
    """Refuse a guide that matched fewer than `minimum` of the channels (a broken or changed feed)."""
    if match.total and match.match_ratio < minimum:
        raise CheckError(
            f"Guide {match.source!r} matched only {len(match.refs)}/{match.total} channels "
            f"(minimum {minimum:.0%}). Check the feed or the mappings."
        )
