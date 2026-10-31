"""In-memory receiver, guide source and publisher for pipeline and config tests.

Also the smallest working example of each interface.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sat_to_ip.config import Section
from sat_to_ip.model import Channel, ChannelRef, GuideMatch, GuideRef, Lineup, PublishResult


def channel(n: int, name: str | None = None, kind: str = "tv") -> Channel:
    return Channel(key=f"fake:{n}", name=name or f"Ch {n}", kind=kind, stream_url=f"http://rx/{n}", number=n)  # type: ignore[arg-type]


@dataclass
class FakeReceiver:
    """A receiver with every optional capability. Its saved list is `channels`; `found` are extra scanned ones."""

    channels: list[Channel] = field(default_factory=lambda: [channel(1), channel(2), channel(3, kind="radio")])
    found: list[Channel] = field(default_factory=list)
    name: str = "fake"
    refreshed: int = 0
    timeshares: list[frozenset[str]] = field(default_factory=list)  # what the last refresh was given
    saved: list[tuple[ChannelRef, ...]] = field(default_factory=list)
    fail_on: str | None = None
    unstreamable: frozenset[str] = frozenset()  # saved channels lineup() leaves out (no stream)

    def _maybe_fail(self, what: str) -> None:
        if self.fail_on == what:
            raise RuntimeError(f"{what} broke")

    def lineup(self) -> Lineup:
        self._maybe_fail("lineup")
        return Lineup(tuple(c for c in self.channels if c.name not in self.unstreamable))

    def refresh(self, timeshares: Sequence[frozenset[str]] = ()) -> None:
        self._maybe_fail("refresh")
        self.refreshed += 1
        self.timeshares = list(timeshares)

    def selection_candidates(self) -> tuple[list[ChannelRef], list[ChannelRef]]:
        self._maybe_fail("select")

        def ref(c: Channel) -> ChannelRef:
            return ChannelRef(c.key, c.name, c.kind, c.frequency_mhz, c.service_id)

        return [ref(c) for c in self.channels], [ref(c) for c in self.found]

    def save_selection(self, channels: Sequence[ChannelRef]) -> None:
        self.saved.append(tuple(channels))


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
        return PublishResult(count, "dry run" if dry_run else "")


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
