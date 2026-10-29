"""The three seams. Implement one of these (and add it to registry.py) to support a new product.

Builders (`from_config`) must not do I/O: config is validated with `sat-to-ip check-config`
before anything is contacted. Log in and connect lazily, on first use.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sat_to_ip.model import ChannelRef, GuideMatch, Lineup, PublishResult


class Receiver(Protocol):
    """A SAT>IP receiver (or anything else) that can say which channels it streams."""

    name: str

    def lineup(self) -> Lineup:
        """The receiver's saved channels with stream URLs, read live (it runs straight after a save).

        - Each Channel.key is unique and stable, and the same key selection_candidates() uses for it.
        - kind is "tv" or "radio"; leave number at 0 (the pipeline numbers the lineup).
        - A saved channel the receiver can't stream may be left out with a warning; the pipeline refuses
          to publish if too many are missing. Raise IntegrationError if the device can't be read at all.
        """
        ...


@runtime_checkable
class SupportsRefresh(Protocol):
    """A receiver that can update its transponder list and rescan.

    timeshares: the channel list's timeshares, each the set of its names in lowercase; a scan can find
    one under any of them.
    """

    def refresh(self, timeshares: Sequence[frozenset[str]] = ()) -> None: ...


@runtime_checkable
class SupportsSelection(Protocol):
    """A receiver that keeps a saved channel list the pipeline can change.

    The core does the rest: matching the channel list, the interactive picker, the safety checks.
    """

    def selection_candidates(self) -> tuple[Sequence[ChannelRef], Sequence[ChannelRef]]:
        """(the saved channels in order, other channels the receiver has found), read live."""
        ...

    def save_selection(self, channels: Sequence[ChannelRef]) -> None:
        """Make exactly these the receiver's saved channels, in this order (keys from selection_candidates)."""
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

        dry_run: build and check everything, make no changes (no write APIs, no files); read-only calls are fine.
        """
        ...
