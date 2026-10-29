"""The three seams. Implement one of these (and add it to registry.py) to support a new product.

Builders (`from_config`) must not do I/O: config is validated with `sat-to-ip check-config`
before anything is contacted. Log in and connect lazily, on first use.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sat_to_ip.model import GuideMatch, Lineup, PublishResult, SelectionPlan


class Receiver(Protocol):
    """A SAT>IP receiver (or anything else) that can say which channels it streams."""

    name: str

    def lineup(self) -> Lineup:
        """The receiver's saved channels, in order, with stream URLs. Read live; no caching across runs."""
        ...


@runtime_checkable
class SupportsRefresh(Protocol):
    """A receiver that can update its transponder list and rescan."""

    def refresh(self) -> None: ...


@runtime_checkable
class SupportsSelection(Protocol):
    """A receiver that keeps a saved channel list the pipeline can change."""

    def plan_selection(self, specs: Sequence[str]) -> SelectionPlan:
        """Which channels the specs select, versus what is saved now. Changes nothing."""
        ...

    def plan_selection_interactive(self) -> SelectionPlan | None:
        """Let a person pick channels in the terminal. None if they cancel. Changes nothing."""
        ...

    def apply_selection(self, plan: SelectionPlan) -> None:
        """Save plan.proposed as the receiver's channel list, in that order."""
        ...


class GuideSource(Protocol):
    """Somewhere programme guide data comes from."""

    name: str
    kind: str  # "gracenote" | "xmltv"; publishers declare which kinds they accept

    def match(self, lineup: Lineup) -> GuideMatch: ...


class Publisher(Protocol):
    """A DVR or media server the lineup is published to."""

    name: str
    guide: str | None  # name of the guide source this target uses, if any
    accepts_guides: frozenset[str]

    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult:
        """Publish matched channels (unmatched ones are left out when a guide is used).

        dry_run: make no changes anywhere (no write APIs, no files outside a temp dir).
        """
        ...
