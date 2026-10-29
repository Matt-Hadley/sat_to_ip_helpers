"""Channel lists: read the channels file, match it against a receiver's channels, and number the lineup.

One entry per line, in channel order; blank lines and '#' comments are ignored:

    BBC One HD            a channel by name (the first one with that name)
    BBC One HD@10773      that name on that frequency (MHz), when a name appears more than once
    all / tv / radio      every channel / every TV / every radio channel not already listed
    101 = BBC One HD      any entry can start with "<number> =" to set its channel number;
                          entries after it count up from there

Numbers stay stable: a listed channel that's missing from the receiver keeps its number free, so the
channels after it don't move.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import ChannelRef, Kind, Lineup, SelectionPlan

KEYWORDS: dict[str, Kind | None] = {"all": None, "tv": "tv", "radio": "radio"}
_NUMBERED = re.compile(r"^(\d+)\s*=\s*(.*)$")
_FREQ_TOLERANCE_MHZ = 0.5  # receivers report e.g. 10773, 10773.0 or 10773.25 for the same transponder


@dataclass(frozen=True)
class Spec:
    """One entry of a channel list."""

    text: str
    line: int = 0
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


def parse_spec(text: str, line: int = 0) -> Spec:
    """Parse one entry; ConfigError says which line is wrong."""
    where = f"line {line}: " if line else ""
    number = None
    entry = text.strip()
    if m := _NUMBERED.match(entry):
        number, entry = int(m.group(1)), m.group(2).strip()
        if number < 1:
            raise ConfigError(f"{where}channel numbers start at 1, got {number}")
        if not entry:
            raise ConfigError(f"{where}{text.strip()!r} has a number but no channel")
    if entry.lower() in KEYWORDS:
        return Spec(text.strip(), line, number, keyword=entry.lower())
    name, at, freq = entry.rpartition("@") if "@" in entry else (entry, "", "")
    if not at:
        return Spec(text.strip(), line, number, name=entry)
    try:
        frequency = float(freq)
    except ValueError:
        raise ConfigError(f"{where}{text.strip()!r}: the part after @ must be a frequency in MHz") from None
    if not name.strip():
        raise ConfigError(f"{where}{text.strip()!r}: missing the channel name before @")
    return Spec(text.strip(), line, number, name=name.strip(), frequency_mhz=frequency)


def read_spec_file(path: Path) -> list[Spec]:
    """Read and check a channel list. Numbers must go up: a number lower than the line before is an error."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"Can't read channel list {path}: {exc}") from exc
    specs = [
        parse_spec(raw, i) for i, raw in enumerate(lines, start=1) if raw.strip() and not raw.lstrip().startswith("#")
    ]
    next_number, prev = 1, None
    for spec in specs:
        if spec.number is not None:
            if spec.number < next_number:
                assert prev is not None
                raise ConfigError(
                    f"{path}: line {spec.line}: number {spec.number} isn't higher than line {prev.line}'s; "
                    "numbers must go up through the file"
                )
            next_number = spec.number
        next_number += 0 if spec.keyword else 1  # how many channels a keyword takes isn't known until run time
        prev = spec
    return specs


def match_specs(specs: Sequence[Spec], channels: Sequence[ChannelRef]) -> list[tuple[Spec, list[ChannelRef]]]:
    """Each spec with the channels it selects, in order. A channel is only ever selected once.

    'Name' takes the first match; 'Name@MHz' takes every match on that frequency; keywords take every
    channel of that kind not already selected.
    """
    taken: set[str] = set()
    result = []
    for spec in specs:
        found = [ch for ch in channels if ch.key not in taken and spec.matches(ch)]
        if spec.keyword is None and spec.frequency_mhz is None:
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

    With a channel list: an entry's own number, else one more than the entry before. A missing channel
    keeps its number free so later channels don't move. Channels the list doesn't mention come after.
    Without a list: 1, 2, 3, … in the receiver's order.
    """
    refs = [ChannelRef(ch.key, ch.name, ch.kind, ch.frequency_mhz) for ch in lineup]
    numbers: dict[str, int] = {}
    next_number = 1
    for spec, found in match_specs(specs or (), refs):
        if spec.number is not None:
            if spec.number < next_number:
                raise CheckError(
                    f"Channel list line {spec.line}: number {spec.number} is already taken by the channels before it "
                    f"(a keyword like 'tv' matched more channels than there was room for). Give it a higher number."
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
