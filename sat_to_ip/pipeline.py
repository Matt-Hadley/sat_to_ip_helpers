"""Run the stages against whatever the config built. The only module that knows how they connect.

Failure rules:
- refresh or select failing aborts the run: the receiver's state is then uncertain.
- In publish, a guide source failing (or failing its check) fails only the publishers using it,
  and each publisher is isolated from the others.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from sat_to_ip.checks import (
    check_guide_match,
    check_lineup,
    check_lineup_complete,
    check_removals,
    check_selection_not_empty,
)
from sat_to_ip.config import Config
from sat_to_ip.exceptions import PipelineError
from sat_to_ip.interfaces import SupportsRefresh, SupportsSelection
from sat_to_ip.model import GuideMatch, Lineup, SelectionPlan
from sat_to_ip.selection import number_lineup, plan_selection

logger = logging.getLogger(__name__)

Status = Literal["ok", "failed", "skipped"]


class Stage(StrEnum):
    REFRESH = "refresh"
    SELECT = "select"
    PUBLISH = "publish"


ALL_STAGES = frozenset(Stage)


@dataclass(frozen=True)
class RunOptions:
    stages: frozenset[Stage] = ALL_STAGES
    allow_removals: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class Outcome:
    name: str
    status: Status
    detail: str = ""


@dataclass
class RunReport:
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str = "") -> None:
        self.outcomes.append(Outcome(name, status, detail))
        log = logger.error if status == "failed" else logger.info
        log(
            f"{'✅' if status == 'ok' else '❌' if status == 'failed' else '⏭️ '} {name}: {status}{' — ' + detail if detail else ''}"
        )

    @property
    def ok(self) -> bool:
        return all(o.status != "failed" for o in self.outcomes)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def summary(self) -> str:
        width = max((len(o.name) for o in self.outcomes), default=0)
        return "\n".join(f"  {o.name:<{width}}  {o.status:<7}  {o.detail}".rstrip() for o in self.outcomes)


def run(config: Config, options: RunOptions | None = None) -> RunReport:
    options = options or RunOptions()
    report = RunReport()
    if options.dry_run:
        logger.info("Dry run: nothing will be changed")
    for stage, step in ((Stage.REFRESH, _refresh), (Stage.SELECT, _select)):
        if stage not in options.stages:
            continue
        try:
            step(config, options, report)
        except Exception as exc:  # any failure here leaves the receiver in an unknown state
            _record_failure(report, stage, exc)
            if Stage.PUBLISH in options.stages:
                report.add(Stage.PUBLISH, "skipped", f"{stage} failed")
            return report
    if Stage.PUBLISH in options.stages:
        _publish(config, options, report)
    return report


def _record_failure(report: RunReport, name: str, exc: BaseException) -> None:
    if not isinstance(exc, PipelineError):
        logger.debug(f"{name} failed", exc_info=exc)
    report.add(name, "failed", _describe(exc))


def _describe(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, PipelineError) else f"{type(exc).__name__}: {exc}"


def _refresh(config: Config, options: RunOptions, report: RunReport) -> None:
    receiver = config.receiver
    if not isinstance(receiver, SupportsRefresh):
        report.add(Stage.REFRESH, "skipped", f"{receiver.name} can't refresh")
        return
    if options.dry_run:
        report.add(Stage.REFRESH, "skipped", "dry run")
        return
    receiver.refresh()
    report.add(Stage.REFRESH, "ok")


def _select(config: Config, options: RunOptions, report: RunReport) -> None:
    receiver = config.receiver
    if not isinstance(receiver, SupportsSelection):
        report.add(Stage.SELECT, "skipped", f"{receiver.name} has no saved channel list")
        return
    if config.channel_specs is None:
        report.add(Stage.SELECT, "skipped", "no [[channels]] in the config")
        return

    saved, available = receiver.selection_candidates()
    plan = plan_selection(saved, available, config.channel_specs)
    _log_plan(plan)
    check_selection_not_empty(plan)
    check_removals(plan, config.checks.max_removed, allow=options.allow_removals)
    if [ch.key for ch in plan.proposed] == [ch.key for ch in plan.current]:
        report.add(Stage.SELECT, "ok", f"{len(plan.proposed)} channels, unchanged")
        return
    if options.dry_run:
        report.add(Stage.SELECT, "ok", f"dry run: would save {len(plan.proposed)} channels")
        return
    receiver.save_selection(plan.proposed)
    report.add(Stage.SELECT, "ok", f"{len(plan.proposed)} channels (+{len(plan.added)} / -{len(plan.removed)})")


def _log_plan(plan: SelectionPlan) -> None:
    logger.info(f"Selection: {len(plan.proposed)} channels, +{len(plan.added)} / -{len(plan.removed)}")
    for ch in plan.added:
        logger.info(f"   + {ch.name}")
    for ch in plan.removed:
        logger.warning(f"   - {ch.name}")
    for spec in plan.unmatched_specs:
        logger.warning(f"   No channel matches {spec!r}")


def _publish(config: Config, options: RunOptions, report: RunReport) -> None:
    try:
        lineup = config.receiver.lineup()
        check_lineup(lineup)
        if isinstance(config.receiver, SupportsSelection):
            saved, _ = config.receiver.selection_candidates()
            check_lineup_complete(saved, lineup, config.checks.max_removed, allow=options.allow_removals)
        lineup = number_lineup(lineup, config.channel_specs)
    except Exception as exc:
        _record_failure(report, Stage.PUBLISH, exc)
        return
    logger.info(f"Lineup: {len(lineup.tv)} TV, {len(lineup.radio)} radio")

    matches, guide_errors = _match_guides(config, lineup, {p.guide for p in config.publishers if p.guide})
    for pub in config.publishers:
        target = f"publish:{pub.name}"
        if pub.guide in guide_errors:
            report.add(target, "failed", f"guide {pub.guide!r}: {guide_errors[pub.guide]}")
            continue
        try:
            result = pub.publish(lineup, matches.get(pub.guide) if pub.guide else None, dry_run=options.dry_run)
        except Exception as exc:
            _record_failure(report, target, exc)
            continue
        report.add(target, "ok", f"{result.channels} channels{'; ' + result.detail if result.detail else ''}")


def _match_guides(config: Config, lineup: Lineup, names: Iterable[str]) -> tuple[dict[str, GuideMatch], dict[str, str]]:
    matches: dict[str, GuideMatch] = {}
    errors: dict[str, str] = {}
    for name in sorted(names):
        try:
            match = config.guides[name].match(lineup)
            for ch in match.unmatched:
                logger.warning(f"   Guide {name!r}: no match for {ch.name!r}; left out of its targets")
            check_guide_match(match, config.checks.min_guide_match)
        except Exception as exc:
            if not isinstance(exc, PipelineError):
                logger.debug(f"guide {name} failed", exc_info=exc)
            errors[name] = _describe(exc)
            continue
        logger.info(f"Guide {name!r}: matched {len(match.refs)}/{match.total}")
        matches[name] = match
    return matches, errors
