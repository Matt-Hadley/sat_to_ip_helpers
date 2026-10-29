"""Channel lists: read the [[channels]] blocks from the config, match them against a receiver's channels,
and number the lineup.

    [[channels]]
    start = 101                                   # the first channel's number; the rest count up
    names = [
      "BBC One HD",                               # a channel by name (the first one with that name)
      { name = "BBC One HD", frequency = 10773 }, # that name on that frequency (MHz), when it appears twice
      { rest = "tv" },                            # every TV / radio / channel ("all") not listed before
    ]

Numbers stay stable: a listed channel that's missing from the receiver keeps its number free, so the
channels after it don't move.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import ChannelRef, Kind, Lineup, SelectionPlan

KEYWORDS: dict[str, Kind | None] = {"all": None, "tv": "tv", "radio": "radio"}
_FREQ_TOLERANCE_MHZ = 0.5  # receivers report e.g. 10773, 10773.0 or 10773.25 for the same transponder


@dataclass(frozen=True)
class Spec:
    """One entry of a channel block."""

    text: str  # how to name it in messages, e.g. "BBC One HD@10773"
    where: str = ""  # its place in the config, e.g. "channels[0].names[3]"
    number: int | None = None
    keyword: str | None = None
    name: str | None = None
    frequency_mhz: float | None = None

    def matches(self, ch: ChannelRef) -> bool:
        if self.keyword is not None:
            return KEYWORDS[self.keyword] in (None, ch.kind)
        if self.name is None or ch.name.lower() != self.name.lower():
            return False
        if self.frequency_mhz is None:
            return True
        return ch.frequency_mhz is not None and abs(ch.frequency_mhz - self.frequency_mhz) < _FREQ_TOLERANCE_MHZ


def _entry(value: Any, where: str, number: int | None) -> Spec:
    if isinstance(value, str):
        if not value.strip():
            raise ConfigError(f"{where}: must not be empty")
        return Spec(value.strip(), where, number, name=value.strip())
    if not isinstance(value, dict):
        raise ConfigError(f'{where}: must be a channel name or a table like {{ name = "…" }}, got {value!r}')
    unknown = sorted(set(value) - {"name", "frequency", "rest"})
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
    if "rest" in value:
        rest = value["rest"]
        if set(value) != {"rest"} or rest not in KEYWORDS:
            raise ConfigError(f'{where}: use {{ rest = "tv" }}, "radio" or "all" on its own, got {value!r}')
        return Spec(f"rest = {rest}", where, number, keyword=rest)
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}.name: required, the channel name as your receiver shows it")
    frequency = value.get("frequency")
    if frequency is None:
        return Spec(name.strip(), where, number, name=name.strip())
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)):
        raise ConfigError(f"{where}.frequency: must be a number in MHz, got {frequency!r}")
    return Spec(f"{name.strip()}@{frequency:g}", where, number, name=name.strip(), frequency_mhz=float(frequency))


def specs_from_config(blocks: Any) -> list[Spec] | None:
    """Turn the [[channels]] blocks into entries. None when there are none (nothing to select)."""
    if blocks is None:
        return None
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        raise ConfigError("channels: must be [[channels]] blocks, each with names = [ … ]")
    specs: list[Spec] = []
    next_number, prev = 1, ""
    for i, block in enumerate(blocks):
        where = f"channels[{i}]"
        unknown = sorted(set(block) - {"start", "names"})
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
        start = block.get("start")
        if start is not None and (isinstance(start, bool) or not isinstance(start, int) or start < 1):
            raise ConfigError(f"{where}.start: must be a channel number of 1 or more, got {start!r}")
        if start is not None and start < next_number:
            raise ConfigError(
                f"{where}.start: {start} overlaps {prev} (its channels run up to {next_number - 1}); "
                "start it higher or move channels between blocks"
            )
        names = block.get("names")
        if not isinstance(names, list) or not names:
            raise ConfigError(f"{where}.names: required, a list of channel names")
        for j, value in enumerate(names):
            spec = _entry(value, f"{where}.names[{j}]", start if j == 0 else None)
            specs.append(spec)
            # how many channels a `rest` entry takes is only known at run time; number_lineup checks that
            next_number = (spec.number or next_number) + (0 if spec.keyword else 1)
        prev = where
    return specs


def match_specs(specs: Sequence[Spec], channels: Sequence[ChannelRef]) -> list[tuple[Spec, list[ChannelRef]]]:
    """Each entry with the channels it selects, in order. A channel is only ever selected once.

    A name (with or without a frequency) takes the first match not already selected, so listing it twice
    takes two channels of that name; `rest` takes every channel of that kind not already selected.
    """
    taken: set[str] = set()
    result = []
    for spec in specs:
        found = [ch for ch in channels if ch.key not in taken and spec.matches(ch)]
        if spec.keyword is None:
            found = found[:1]
        taken.update(ch.key for ch in found)
        result.append((spec, found))
    return result


def plan_selection(
    saved: Sequence[ChannelRef], available: Sequence[ChannelRef], specs: Sequence[Spec]
) -> SelectionPlan:
    """What saving the channel list would change: proposed channels in list order, plus entries that matched nothing."""
    matched = match_specs(specs, [*saved, *available])
    return SelectionPlan(
        current=tuple(saved),
        proposed=tuple(ch for _, found in matched for ch in found),
        unmatched_specs=tuple(spec.text for spec, found in matched if not found and spec.keyword is None),
    )


def number_lineup(lineup: Lineup, specs: Sequence[Spec] | None) -> Lineup:
    """Give every channel its number, and sort the lineup by it.

    With channel blocks: a block's start, then one more for each entry. A missing channel keeps its
    number free so later channels don't move. Channels the blocks don't mention come after.
    Without blocks: 1, 2, 3, … in the receiver's order.
    """
    refs = [ChannelRef(ch.key, ch.name, ch.kind, ch.frequency_mhz) for ch in lineup]
    numbers: dict[str, int] = {}
    next_number = 1
    for spec, found in match_specs(specs or (), refs):
        if spec.number is not None:
            if spec.number < next_number:
                raise CheckError(
                    f"{spec.where}: start {spec.number} is already taken by the channels before it "
                    f"(a `rest` entry matched more channels than there was room for). Start this block higher."
                )
            next_number = spec.number
        if not found and spec.keyword is None:
            next_number += 1  # keep the missing channel's number free
        for ref in found:
            numbers[ref.key] = next_number
            next_number += 1
    unlisted = max(numbers.values(), default=0) + 1
    numbered = []
    for channel in lineup:
        if channel.key not in numbers:
            numbers[channel.key] = unlisted
            unlisted += 1
        numbered.append(replace(channel, number=numbers[channel.key]))
    return Lineup(tuple(sorted(numbered, key=lambda c: c.number)))
