"""In-memory receiver, guide source and publisher for pipeline and config tests.

Also the smallest working example of each interface.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sat_to_ip.config import Section
from sat_to_ip.model import Channel, ChannelRef, GuideMatch, GuideRef, Lineup, PublishResult, SelectionPlan


def channel(n: int, name: str | None = None, kind: str = "tv") -> Channel:
    return Channel(key=f"fake:{n}", name=name or f"Ch {n}", kind=kind, stream_url=f"http://rx/{n}", number=n)  # type: ignore[arg-type]


@dataclass
class FakeReceiver:
    """A receiver with every optional capability."""

    channels: list[Channel] = field(default_factory=lambda: [channel(1), channel(2), channel(3, kind="radio")])
    name: str = "fake"
    refreshed: int = 0
    applied: list[SelectionPlan] = field(default_factory=list)
    interactive_plan: SelectionPlan | None = None
    fail_on: str | None = None

    def _maybe_fail(self, what: str) -> None:
        if self.fail_on == what:
            raise RuntimeError(f"{what} broke")

    def lineup(self) -> Lineup:
        self._maybe_fail("lineup")
        return Lineup(tuple(self.channels))

    def refresh(self) -> None:
        self._maybe_fail("refresh")
        self.refreshed += 1

    def plan_selection(self, specs: Sequence[str]) -> SelectionPlan:
        self._maybe_fail("select")
        current = tuple(ChannelRef(c.key, c.name) for c in self.channels)
        proposed = tuple(r for r in current if r.name in specs)
        missing = tuple(s for s in specs if s not in {r.name for r in current})
        return SelectionPlan(current, proposed, missing)

    def plan_selection_interactive(self) -> SelectionPlan | None:
        return self.interactive_plan

    def apply_selection(self, plan: SelectionPlan) -> None:
        self.applied.append(plan)


@dataclass
class BasicReceiver:
    """A receiver with no optional capabilities."""

    name: str = "basic"

    def lineup(self) -> Lineup:
        return Lineup((channel(1),))


@dataclass
class FakeGuide:
    name: str = "guide"
    kind: str = "fake"
    unmatched_names: frozenset[str] = frozenset()
    fail: bool = False
    calls: int = 0

    def match(self, lineup: Lineup) -> GuideMatch:
        self.calls += 1
        if self.fail:
            raise RuntimeError("feed down")
        refs = {c.key: GuideRef(f"id-{c.name}") for c in lineup if c.name not in self.unmatched_names}
        unmatched = tuple(c for c in lineup if c.name in self.unmatched_names)
        return GuideMatch(self.name, self.kind, refs, unmatched)


@dataclass
class FakePublisher:
    name: str = "pub"
    guide: str | None = "guide"
    accepts_guides: frozenset[str] = frozenset({"fake"})
    fail: bool = False
    published: list[tuple[Lineup, GuideMatch | None, bool]] = field(default_factory=list)

    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult:
        if self.fail:
            raise RuntimeError("DVR down")
        self.published.append((lineup, guide, dry_run))
        count = len(guide.refs) if guide else len(lineup)
        return PublishResult(self.name, count, "dry run" if dry_run else "")


# Builders for registry/config tests -------------------------------------------------------------


def build_receiver(section: Section) -> FakeReceiver:
    return FakeReceiver(name=section.get_str("label", "fake"))


def build_guide(section: Section) -> FakeGuide:
    return FakeGuide(name=section.name, kind=section.get_str("kind", "fake"))


def build_publisher(section: Section) -> FakePublisher:
    return FakePublisher(
        name=section.name,
        guide=section.get_optional_str("guide"),
        accepts_guides=frozenset(section.get_str_list("accepts", ["fake"])),
    )
