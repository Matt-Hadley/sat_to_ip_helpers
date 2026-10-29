"""Interactive channel picker (curses): tick the channels a receiver should save. Works for any receiver."""

import curses
from collections.abc import Callable

from sat_to_ip.model import ChannelRef


def build_entries(
    listed: list[ChannelRef], available: list[ChannelRef]
) -> tuple[list[ChannelRef], list[str], list[bool]]:
    """Return (channels, origin_labels, selected): the ones in your channel list ticked; TV first, then by name.

    origin_labels values: "listed" (in your channel list, or saved on the receiver), "found"
    """
    combined = sorted(
        [(ch, True) for ch in listed] + [(ch, False) for ch in available],
        key=lambda t: (0 if t[0].kind == "tv" else 1, t[0].name.lower()),
    )
    channels = [ch for ch, _ in combined]
    listed_flags = [d for _, d in combined]
    origins = ["listed" if d else "found" for d in listed_flags]
    return channels, origins, list(listed_flags)


def apply_selection_key(key: int, cursor: int, selected: list[bool], channels: list[ChannelRef]) -> None:
    """Mutate *selected* in-place for toggle / bulk-select keys."""
    if key == ord(" "):
        selected[cursor] = not selected[cursor]
        return
    bulk: dict[int, Callable[[ChannelRef], bool]] = {
        ord("a"): lambda _: True,
        ord("n"): lambda _: False,
        ord("t"): lambda ch: ch.kind == "tv",
        ord("r"): lambda ch: ch.kind == "radio",
    }
    if key in bulk:
        predicate = bulk[key]
        for i, ch in enumerate(channels):
            selected[i] = predicate(ch)


def handle_key(
    key: int, cursor: int, list_h: int, selected: list[bool], channels: list[ChannelRef]
) -> tuple[int, bool, bool]:
    """Process a keypress; return (new_cursor, save, cancel)."""
    if key in (ord("q"), 27):
        return cursor, False, True
    if key in (curses.KEY_ENTER, 10, 13):
        return cursor, True, False
    if key in (curses.KEY_UP, ord("k")):
        return max(0, cursor - 1), False, False
    if key in (curses.KEY_DOWN, ord("j")):
        return min(len(channels) - 1, cursor + 1), False, False
    if key == curses.KEY_PPAGE:
        return max(0, cursor - list_h), False, False
    if key == curses.KEY_NPAGE:
        return min(len(channels) - 1, cursor + list_h), False, False
    apply_selection_key(key, cursor, selected, channels)
    return cursor, False, False


def draw(
    stdscr: curses.window,
    channels: list[ChannelRef],
    origins: list[str],
    selected: list[bool],
    cursor: int,
    scroll: int,
) -> None:
    """Render the whole picker screen."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    c_header = curses.color_pair(1)
    c_checked = curses.color_pair(2)
    c_unchecked = curses.color_pair(3)
    c_cursor = curses.color_pair(4)

    header = "  Channels  |  SPACE=toggle  a=all  n=none  t=TV  r=radio  ENTER=save  q=cancel  "
    stdscr.addstr(0, 0, header[: w - 1], c_header | curses.A_BOLD)
    try:
        stdscr.addstr(1, 0, "─" * (w - 1), c_header)
    except curses.error:
        pass

    list_h = h - 4
    for row in range(list_h):
        idx = scroll + row
        if idx >= len(channels):
            break
        ch = channels[idx]
        type_label = "TV" if ch.kind == "tv" else "R "
        freq = f"{ch.frequency_mhz:g}" if ch.frequency_mhz is not None else "?"
        check = "[x]" if selected[idx] else "[ ]"
        line = f" {check} {type_label}  {ch.name:<34} {freq:>8} MHz  {origins[idx]:<7}"
        colour = c_cursor if idx == cursor else (c_checked if selected[idx] else c_unchecked)
        try:
            stdscr.addstr(2 + row, 0, line[: w - 1], colour)
        except curses.error:
            pass

    n_sel = sum(selected)
    n_tv = sum(1 for i, ch in enumerate(channels) if selected[i] and ch.kind == "tv")
    n_radio = sum(1 for i, ch in enumerate(channels) if selected[i] and ch.kind == "radio")
    footer = (
        f"  Selected: {n_sel}/{len(channels)}    TV: {n_tv}    Radio: {n_radio}    row {cursor + 1}/{len(channels)}  "
    )
    try:
        stdscr.addstr(h - 2, 0, "─" * (w - 1), c_header)
        stdscr.addstr(h - 1, 0, footer[: w - 1], c_header)
    except curses.error:
        pass
    stdscr.refresh()


def pick(listed: list[ChannelRef], available: list[ChannelRef]) -> list[ChannelRef] | None:
    """Let a person tick channels: listed ones start ticked, newly found ones unticked.

    Returns the ticked channels, or None if they cancelled.

    Keys:
      ↑/↓ / j/k  navigate        Space  toggle
      a/n         select all / none      t/r  TV / radio only
      Enter       save            q/Esc  cancel
    """
    channels, origins, selected = build_entries(listed, available)
    outcome: dict[str, list[ChannelRef] | None] = {"result": None}

    def _run(stdscr: curses.window) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_WHITE, -1)
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)

        cursor, scroll = 0, 0
        while True:
            h, _ = stdscr.getmaxyx()
            list_h = h - 4
            scroll = max(0, min(scroll, cursor))
            if cursor >= scroll + list_h:
                scroll = cursor - list_h + 1
            draw(stdscr, channels, origins, selected, cursor, scroll)
            cursor, save, cancel = handle_key(stdscr.getch(), cursor, list_h, selected, channels)
            if cancel:
                return
            if save:
                outcome["result"] = [channels[i] for i, sel in enumerate(selected) if sel]
                return

    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        return None

    return outcome["result"]
