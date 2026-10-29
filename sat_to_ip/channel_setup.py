"""`sat-to-ip channels`: tick channels from what the receiver has found, and save them as [[channels]] blocks.

Only the [[channels]] part of the config is rewritten; tomlkit keeps every other setting and comment as it was.
"""

import tomllib
from collections.abc import Callable, Sequence
from typing import Any

import tomlkit

from sat_to_ip import channel_picker
from sat_to_ip.config import Config
from sat_to_ip.exceptions import ConfigError
from sat_to_ip.files import write_atomic
from sat_to_ip.interfaces import SupportsSelection
from sat_to_ip.model import ChannelRef
from sat_to_ip.selection import plan_selection, specs_from_config

Picker = Callable[[list[ChannelRef], list[ChannelRef]], list[ChannelRef] | None]


def choose_channels(
    config: Config,
    *,
    pick: Picker = channel_picker.pick,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> bool:
    """Run the picker and save the result into config.path. Returns whether anything was saved."""
    receiver = config.receiver
    if not isinstance(receiver, SupportsSelection):
        raise ConfigError(f"{receiver.name} can't list its channels; write the [[channels]] blocks by hand instead")

    saved, found = receiver.selection_candidates()
    everything = [*saved, *found]
    current = list(plan_selection(saved, found, config.channel_specs).proposed) if config.channel_specs else list(saved)
    keys = {ch.key for ch in current}
    chosen = pick(current, [ch for ch in everything if ch.key not in keys])
    if not chosen:
        say("Nothing saved." if chosen is None else "No channels ticked; nothing saved.")
        return False

    # Keep the existing order for channels already listed; newly ticked ones go after them
    chosen_keys = {ch.key for ch in chosen}
    ordered = [ch for ch in current if ch.key in chosen_keys] + [ch for ch in chosen if ch.key not in keys]
    tv = [ch for ch in ordered if ch.kind == "tv"]
    radio = [ch for ch in ordered if ch.kind == "radio"]

    starts = [spec.number for spec in config.channel_specs or () if spec.number is not None]
    blocks: list[tuple[str, int, list[ChannelRef]]] = []
    next_free = 1
    for label, channels, default in (("TV", tv, starts[0] if starts else 101), ("radio", radio, None)):
        if not channels:
            continue
        if default is None:
            default = starts[1] if len(starts) > 1 and starts[1] >= next_free else max(801, _round_up(next_free))
        start = _ask_number(ask, say, f"First {label} channel number", max(default, next_free), next_free)
        if start is None:
            say("Nothing saved.")
            return False
        blocks.append((label, start, channels))
        next_free = start + len(channels)

    text = _with_blocks(config.path.read_text(encoding="utf-8"), blocks, everything)
    specs_from_config(tomllib.loads(text)["channels"])  # never write a config that wouldn't load
    write_atomic(config.path, text)
    summary = ", ".join(f"{label} {start}–{start + len(chs) - 1}" for label, start, chs in blocks)
    say(f"Saved {len(ordered)} channels to {config.path} ({summary}).")
    say(f"Run `sat-to-ip run -c {config.path}` to apply them.")
    return True


def _round_up(n: int) -> int:
    """The next 'round' block start after n, e.g. 176 → 201."""
    return (n + 99) // 100 * 100 + 1 if n % 100 != 1 else n


def _ask_number(
    ask: Callable[[str], str], say: Callable[[str], None], prompt: str, default: int, minimum: int
) -> int | None:
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
    """The channel's name, or { name, frequency } when another channel has the same name."""
    same_name = sum(1 for other in everything if other.name.lower() == ch.name.lower())
    if same_name > 1 and ch.frequency_mhz is not None:
        entry = tomlkit.inline_table()
        entry.update({"name": ch.name, "frequency": ch.frequency_mhz})
        return entry
    return ch.name


def _with_blocks(
    text: str, blocks: Sequence[tuple[str, int, Sequence[ChannelRef]]], everything: Sequence[ChannelRef]
) -> str:
    doc = tomlkit.parse(text)
    aot = tomlkit.aot()
    for _, start, channels in blocks:
        table = tomlkit.table()
        table["start"] = start
        names = tomlkit.array()
        names.extend(_entry(ch, everything) for ch in channels)
        table["names"] = names.multiline(True)
        aot.append(table)
    aot[-1].add(tomlkit.nl())  # a blank line before whatever section follows
    if "channels" in doc:
        doc["channels"] = aot  # replaced where it was, so the file keeps its layout
    else:
        doc.append("channels", aot)
    return tomlkit.dumps(doc)
