"""Channel lists: read the [[channels]] blocks from the config, match them against a receiver's channels,
and number the lineup.

    [[channels]]
    start = 101                                   # the first channel's number; the rest count up
    names = [
      "BBC One HD",                               # a channel by name (the first one with that name)
      { name = "BBC One HD", frequency = 10773 }, # that name on that frequency (MHz), when it appears twice
      { name = "5DRAMA", service = 2105 },        # that broadcast service, whatever it's called now
      { name = "BBC Three HD", additional_names = ["CBBC HD"], extra_pids = [264] },  # a timeshare, see below
      { rest = "tv" },                            # every TV / radio / channel ("all") not listed before
    ]

Numbers stay stable: a listed channel that's missing from the receiver keeps its number free, so the
channels after it don't move.

A timeshare is one broadcast slot that two channels take turns on (CBBC by day, BBC Three in the evening):
a scan finds it under whichever name is on air. Its entry matches any of its names, and the channel is
published once per name, numbered in that order, all with the same stream.

A scan only sees the channel on air at the time, so its stream asks for that channel's program table (PMT) only.
`extra_pids` adds the other channel's PMT PID to the stream, without which players can't open it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import Channel, ChannelRef, Kind, Lineup, SelectionPlan

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
    service: str | None = None  # matched instead of the name, which is then only a label
    additional_names: tuple[str, ...] = ()  # a timeshare's other names: matched too, and each published
    extra_pids: tuple[int, ...] = ()  # added to the stream's SAT>IP `pids`

    @property
    def names(self) -> tuple[str, ...]:
        return () if self.name is None else (self.name, *self.additional_names)

    def matches(self, ch: ChannelRef) -> bool:
        if self.keyword is not None:
            return KEYWORDS[self.keyword] in (None, ch.kind)
        if self.service is not None:
            if ch.service_id != self.service:
                return False
        elif ch.name.lower() not in {name.lower() for name in self.names}:
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
    unknown = sorted(set(value) - {"name", "additional_names", "extra_pids", "frequency", "service", "rest"})
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
    if "rest" in value:
        rest = value["rest"]
        if set(value) != {"rest"} or rest not in KEYWORDS:
            raise ConfigError(f'{where}: use {{ rest = "tv" }}, "radio" or "all" on its own, got {value!r}')
        return Spec(f"rest = {rest}", where, number, keyword=rest)
    return _named(value, where, number)


def _named(value: dict[str, Any], where: str, number: int | None) -> Spec:
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}.name: required, the channel name as your receiver shows it")
    service = value.get("service")
    if service is not None and (isinstance(service, bool) or not isinstance(service, int) or service < 0):
        raise ConfigError(f"{where}.service: must be the broadcast service ID, a whole number, got {service!r}")
    additional = value.get("additional_names", [])
    if not isinstance(additional, list) or not all(isinstance(n, str) and n.strip() for n in additional):
        raise ConfigError(f"{where}.additional_names: must be a list of channel names, got {additional!r}")
    pids = value.get("extra_pids", [])
    if not isinstance(pids, list) or not all(type(pid) is int and 0 <= pid < 8192 for pid in pids):
        raise ConfigError(f"{where}.extra_pids: must be a list of PIDs, whole numbers from 0 to 8191, got {pids!r}")
    frequency = value.get("frequency")
    if frequency is not None and (isinstance(frequency, bool) or not isinstance(frequency, (int, float))):
        raise ConfigError(f"{where}.frequency: must be a number in MHz, got {frequency!r}")
    text = (
        "/".join(n.strip() for n in [name, *additional])
        + (f"@{frequency:g}" if frequency is not None else "")
        + (f"#{service}" if service is not None else "")
    )
    return Spec(
        text,
        where,
        number,
        name=name.strip(),
        frequency_mhz=float(frequency) if frequency is not None else None,
        service=str(service) if service is not None else None,
        additional_names=tuple(n.strip() for n in additional),
        extra_pids=tuple(pids),
    )


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
            next_number = (spec.number or next_number) + len(spec.names)
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


def timeshares(specs: Sequence[Spec]) -> list[frozenset[str]]:
    """Each timeshare entry's names, in lowercase."""
    return [frozenset(name.lower() for name in spec.names) for spec in specs if spec.additional_names]


def _with_pids(url: str, pids: Sequence[int]) -> str:
    """The SAT>IP stream URL asking for these PIDs too. Unchanged without a `pids` list (none, or "all")."""
    parts = urlsplit(url)
    params = parts.query.split("&")
    for i, param in enumerate(params):
        key, _, value = param.partition("=")
        if key == "pids" and value != "all":
            have = value.split(",")
            params[i] = "pids=" + ",".join([*have, *(str(pid) for pid in pids if str(pid) not in have)])
    return urlunsplit(parts._replace(query="&".join(params)))


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
    A timeshare is published once per name, under its entry's names whatever the receiver calls it now;
    the copies after the first are keyed "<key>/<name>".
    Without blocks: 1, 2, 3, … in the receiver's order.
    """
    refs = [ChannelRef(ch.key, ch.name, ch.kind, ch.frequency_mhz, ch.service_id) for ch in lineup]
    by_key = {ch.key: ch for ch in lineup}
    numbered: list[Channel] = []
    next_number = 1
    for spec, found in match_specs(specs or (), refs):
        if spec.number is not None:
            if spec.number < next_number:
                raise CheckError(
                    f"{spec.where}: start {spec.number} is already taken by the channels before it "
                    f"(a `rest` entry matched more channels than there was room for). Start this block higher."
                )
            next_number = spec.number
        if not found:
            next_number += len(spec.names)  # keep the missing channel's numbers free
        for ref in found:
            channel = by_key.pop(ref.key)
            if spec.extra_pids:
                channel = replace(channel, stream_url=_with_pids(channel.stream_url, spec.extra_pids))
            names = spec.names if spec.additional_names else (channel.name,)
            for i, name in enumerate(names):
                key = f"{channel.key}/{name}" if i else channel.key
                numbered.append(replace(channel, key=key, name=name, number=next_number))
                next_number += 1
    unlisted = max((ch.number for ch in numbered), default=0) + 1
    numbered += [replace(ch, number=n) for n, ch in enumerate(by_key.values(), unlisted)]
    return Lineup(tuple(sorted(numbered, key=lambda c: c.number)))
