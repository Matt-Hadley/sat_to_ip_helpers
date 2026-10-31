"""`sat-to-ip channels`: tick channels from what the receiver has found, and save them as [[channels]] blocks.

Only the [[channels]] part of the config is rewritten; tomlkit keeps every other setting and comment as it was,
and existing blocks are edited in place, so comments on the entries that stay are kept too.
New entries name the broadcast service (`service`) when the receiver knows it, so they keep matching after a rename.
Existing blocks keep their start, their order and their entries, so channel numbers don't move: an unticked
channel is dropped, a listed channel the receiver doesn't have stays (it keeps its number free), and newly
ticked channels go at the end of the last block holding channels of their kind.
"""

import tomllib
from collections.abc import Callable, Sequence
from typing import Any

import tomlkit

from sat_to_ip import channel_picker
from sat_to_ip.config import Config
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.files import write_atomic
from sat_to_ip.interfaces import SupportsSelection
from sat_to_ip.model import ChannelRef, Kind
from sat_to_ip.selection import Spec, match_specs, specs_from_config

Picker = Callable[[list[ChannelRef], list[ChannelRef]], list[ChannelRef] | None]
Ask = Callable[[str], str]
Say = Callable[[str], None]
KINDS: tuple[tuple[Kind, str], ...] = (("tv", "TV"), ("radio", "radio"))


class _Block:
    """One [[channels]] block being written: its start (None: carries on from the block before) and entries.

    An existing block keeps its tomlkit `table`, edited in place; `entries` are then the ones to append to it.
    """

    def __init__(self, start: int | None, entries: list[Any] | None = None, size: int = 0, table: Any = None):
        self.start = start
        self.entries = entries if entries is not None else []
        self.size = size  # how many numbers it takes: ticked channels plus kept placeholders
        self.kinds: set[Kind] = set()
        self.table = table

    def label(self) -> str:
        kinds = " + ".join(k if k == "radio" else "TV" for k in sorted(self.kinds, reverse=True))
        numbers = f" {self.start}–{self.start + self.size - 1}" if self.start is not None else ""
        return f"{kinds or 'channels'}{numbers}"


def choose_channels(
    config: Config,
    *,
    pick: Picker = channel_picker.pick,
    ask: Ask = input,
    say: Say = print,
) -> bool:
    """Run the picker and save the result into config.path. Returns whether anything was saved."""
    receiver = config.receiver
    if not isinstance(receiver, SupportsSelection):
        raise ConfigError(f"{receiver.name} can't list its channels; write the [[channels]] blocks by hand instead")

    saved, found = receiver.selection_candidates()
    everything = [*saved, *found]
    if not everything:
        raise IntegrationError(
            f"{receiver.name} has no channels yet; scan first with: sat-to-ip run -c {config.path} --stages refresh"
        )
    specs = config.channel_specs
    matched = match_specs(specs, everything) if specs else []
    current = [ch for _, chs in matched for ch in chs] if specs else list(saved)
    keys = {ch.key for ch in current}
    chosen = pick(current, [ch for ch in everything if ch.key not in keys])
    if not chosen:
        say("Nothing saved." if chosen is None else "No channels ticked; nothing saved.")
        return False

    chosen_keys = {ch.key for ch in chosen}
    new = [ch for ch in chosen if ch.key not in keys]
    text = config.path.read_text(encoding="utf-8")
    # `matched` lines up with the blocks as loaded; edited since, the new blocks would get the wrong entries
    if specs_from_config(tomllib.loads(text).get("channels")) != (list(specs) if specs is not None else None):
        raise ConfigError(f"{config.path}: [[channels]] changed while picking; nothing saved. Run this again.")
    doc = tomlkit.parse(text)
    if specs:
        blocks = _edit_blocks(doc["channels"], matched, chosen_keys, new, everything, ask, say)
    else:
        # channels already saved keep their order; newly ticked ones follow
        ordered = [ch for ch in current if ch.key in chosen_keys] + new
        blocks = _new_blocks(ordered, everything, ask, say)
    if blocks is None:
        say("Nothing saved.")
        return False

    text = _with_blocks(doc, blocks)
    specs_from_config(tomllib.loads(text)["channels"])  # never write a config that wouldn't load
    write_atomic(config.path, text)
    say(f"Saved {len(chosen)} channels to {config.path} ({', '.join(b.label() for b in blocks)}).")
    kept = sum(1 for spec, chs in matched if not chs and spec.keyword is None)
    if kept:
        say(f"Kept {kept} listed channel(s) the receiver doesn't have, so the numbers after them don't move.")
    say(f"Run `sat-to-ip run -c {config.path}` to apply them.")
    return True


def _new_blocks(
    chosen: Sequence[ChannelRef], everything: Sequence[ChannelRef], ask: Ask, say: Say
) -> list[_Block] | None:
    """A first channel list: one TV block and one radio block, starting where the user says."""
    blocks: list[_Block] = []
    next_free = 1
    for kind, label in KINDS:
        channels = [ch for ch in chosen if ch.kind == kind]
        if not channels:
            continue
        default = max(801 if kind == "radio" else 101, _round_up(next_free))
        start = _ask_number(ask, say, f"First {label} channel number", default, next_free)
        if start is None:
            return None
        block = _Block(start, [_entry(ch, everything) for ch in channels], len(channels))
        block.kinds.add(kind)
        blocks.append(block)
        next_free = start + len(channels)
    return blocks


def _edit_blocks(
    tables: Any,
    matched: Sequence[tuple[Spec, list[ChannelRef]]],
    chosen_keys: set[str],
    new: Sequence[ChannelRef],
    everything: Sequence[ChannelRef],
    ask: Ask,
    say: Say,
) -> list[_Block] | None:
    """The existing blocks (tomlkit tables, edited in place) with unticked channels dropped, and newly
    ticked ones appended."""
    entries = iter(matched)  # specs_from_config made one spec per entry, in file order
    blocks: list[_Block] = []
    last_of_kind: dict[Kind, _Block] = {}
    for table in tables:
        names = table["names"]
        block = _Block(table.get("start"), table=table)
        replaced: list[tuple[int, list[Any]]] = []
        for i in range(len(names)):
            spec, chs = next(entries)
            ticked = [ch for ch in chs if ch.key in chosen_keys]
            if chs and len(ticked) < len(chs):  # some of what it matched was unticked: list the rest by name
                replaced.append((i, [_entry(ch, everything) for ch in ticked]))
            # otherwise it stays as written, comment and all: a placeholder, a name, or a whole `rest`
            block.size += len(ticked) * len(spec.names) if chs else len(spec.names)
            block.kinds.update(ch.kind for ch in ticked)
        _replace(names, replaced)
        if not len(names):
            # shortcut: an emptied block is dropped, so a following block without `start` moves up; give
            # every block a start (the picker always does) to avoid that
            continue
        blocks.append(block)
        for kind in block.kinds:
            last_of_kind[kind] = block

    next_free = _next_free(blocks)
    for kind, label in KINDS:
        channels = [ch for ch in new if ch.kind == kind]
        if not channels:
            continue
        target = last_of_kind.get(kind)
        if target is None:
            default = max(801, _round_up(next_free)) if kind == "radio" else _round_up(next_free)
            start = _ask_number(ask, say, f"First {label} channel number", default, next_free)
            if start is None:
                return None
            target = _Block(start)
            blocks.append(target)
        target.entries += [_entry(ch, everything) for ch in channels]
        target.size += len(channels)
        target.kinds.add(kind)
        next_free = max(next_free, _next_free(blocks))
    return blocks


def _replace(names: Any, replaced: Sequence[tuple[int, list[Any]]]) -> None:
    """Swap entries of a tomlkit array for others, in place, so the comments on the rest stay."""
    for i, values in reversed(replaced):
        del names[i]
        for offset, value in enumerate(values):
            names.insert(i + offset, _inline(value))


def _next_free(blocks: Sequence[_Block]) -> int:
    number = 1
    for block in blocks:
        number = (block.start or number) + block.size
    return number


def _round_up(n: int) -> int:
    """The next 'round' block start from n, e.g. 176 → 201, 101 → 101, 1 → 1."""
    return (n + 99) // 100 * 100 + 1 if n % 100 != 1 else n


def _ask_number(ask: Ask, say: Say, prompt: str, default: int, minimum: int) -> int | None:
    while True:
        try:
            answer = ask(f"{prompt} [{default}]: ").strip()
        except EOFError:
            return None
        if not answer:
            return default
        if answer.isdigit() and int(answer) >= minimum:
            return int(answer)
        say(f"Please enter a number of at least {minimum}.")


def _entry(ch: ChannelRef, everything: Sequence[ChannelRef]) -> Any:
    """{ name, service } when the receiver knows the broadcast service ID, so the entry survives renames;
    plus the frequency when another channel shares it. Just the name when there's nothing more to say."""
    entry: dict[str, Any] = {"name": ch.name}
    if ch.service_id is not None and ch.service_id.isdigit():
        entry["service"] = int(ch.service_id)
        twins = sum(1 for other in everything if other.service_id == ch.service_id)
    else:
        twins = sum(1 for other in everything if other.name.lower() == ch.name.lower())
    if twins > 1 and ch.frequency_mhz is not None:
        entry["frequency"] = ch.frequency_mhz
    return entry if len(entry) > 1 else ch.name


def _inline(value: Any) -> Any:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        table.update(value)
        return table
    return value


def _with_blocks(doc: Any, blocks: Sequence[_Block]) -> str:
    """The config with these blocks as its [[channels]]. Existing blocks are edited where they are, so the file
    keeps its layout; new ones (never before an existing one) go after them."""
    if "channels" not in doc:
        doc.append("channels", tomlkit.aot())
    aot = doc["channels"]
    kept = [id(block.table) for block in blocks if block.table is not None]
    for i in reversed(range(len(aot))):
        if id(aot[i]) not in kept:
            del aot[i]  # emptied
    for block in blocks:
        if block.table is not None:
            block.table["names"].extend(_inline(value) for value in block.entries)
            continue
        table = tomlkit.table()
        if block.start is not None:
            table["start"] = block.start
        names = tomlkit.array()
        names.extend(_inline(value) for value in block.entries)
        table["names"] = names.multiline(True)
        aot.append(table)
    if blocks[-1].table is None:
        aot[-1].add(tomlkit.nl())  # a blank line before whatever section follows
    return tomlkit.dumps(doc)
