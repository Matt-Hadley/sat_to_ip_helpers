"""The data that flows between stages. Receiver-, guide- and DVR-neutral; immutable."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["tv", "radio"]


@dataclass(frozen=True)
class Channel:
    """One channel a receiver can stream.

    key:    stable and unique per receiver (e.g. "octopus:10301"); everything else matches on it.
    number: 1-based position in the receiver's saved list; becomes the channel number.
    """

    key: str
    name: str
    kind: Kind
    stream_url: str
    number: int
    frequency_mhz: float | None = None
    service_id: str | None = None


@dataclass(frozen=True)
class Lineup:
    """The ordered channels a receiver offers right now."""

    channels: tuple[Channel, ...] = ()

    def __len__(self) -> int:
        return len(self.channels)

    def __iter__(self) -> Iterator[Channel]:
        return iter(self.channels)

    def of_kind(self, kind: Kind) -> tuple[Channel, ...]:
        return tuple(ch for ch in self.channels if ch.kind == kind)

    @property
    def tv(self) -> tuple[Channel, ...]:
        return self.of_kind("tv")

    @property
    def radio(self) -> tuple[Channel, ...]:
        return self.of_kind("radio")


@dataclass(frozen=True)
class GuideRef:
    """Where a channel's programme guide lives in one guide source.

    guide_id:   Gracenote call sign, or XMLTV <channel id>.
    station_id: Gracenote station ID (Channels DVR needs it); None for XMLTV.
    """

    guide_id: str
    display_name: str | None = None
    station_id: str | None = None


@dataclass(frozen=True)
class GuideMatch:
    """The result of matching a lineup against one guide source.

    refs:   guide reference per Channel.key, for matched channels only.
    xmltv:  the guide filtered to the matched channels, for publishers that write guide files.
    """

    source: str
    kind: str
    refs: Mapping[str, GuideRef] = field(default_factory=dict)
    unmatched: tuple[Channel, ...] = ()
    xmltv: bytes | None = None

    @property
    def total(self) -> int:
        return len(self.refs) + len(self.unmatched)

    @property
    def match_ratio(self) -> float:
        """Share of channels matched; 1.0 for an empty lineup (nothing to miss)."""
        return len(self.refs) / self.total if self.total else 1.0


@dataclass(frozen=True)
class ChannelRef:
    """A channel in a receiver's saved list, as selection sees it."""

    key: str
    name: str


@dataclass(frozen=True)
class SelectionPlan:
    """What applying a channel selection would change on the receiver."""

    current: tuple[ChannelRef, ...]
    proposed: tuple[ChannelRef, ...]
    unmatched_specs: tuple[str, ...] = ()

    @property
    def removed(self) -> tuple[ChannelRef, ...]:
        keep = {ch.key for ch in self.proposed}
        return tuple(ch for ch in self.current if ch.key not in keep)

    @property
    def added(self) -> tuple[ChannelRef, ...]:
        have = {ch.key for ch in self.current}
        return tuple(ch for ch in self.proposed if ch.key not in have)


@dataclass(frozen=True)
class PublishResult:
    target: str
    channels: int
    detail: str = ""
