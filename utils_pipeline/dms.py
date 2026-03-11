"""DMS channel list helpers and curses-based interactive editor."""
import curses


def merge_dms_selections(dms_channels: list[dict], chosen: list[dict]) -> list[dict]:
    """Build the final DMS list from the current DMS and a new user selection.

    Channels already in the DMS keep their existing position.
    Newly added channels are appended with sequential positions at the end.
    """
    dms_by_id = {ch["serviceid"]: ch for ch in dms_channels}
    next_pos = max((ch.get("position", 0) for ch in dms_channels), default=-1) + 1
    final, new_offset = [], 0
    for ch in chosen:
        sid = ch.get("serviceid")
        if sid and sid in dms_by_id:
            final.append(dms_by_id[sid])
        else:
            ch["position"] = next_pos + new_offset
            new_offset += 1
            final.append(ch)
    return final


def build_dms_entries(
    dms_channels: list[dict], available: list[dict]
) -> tuple[list[dict], list[str], list[bool]]:
    """Return (channels, origin_labels, selected) sorted video-first then by name.

    origin_labels values: "DMS", "available"
    """
    combined = sorted(
        [(ch, True) for ch in dms_channels] + [(ch, False) for ch in available],
        key=lambda t: (0 if t[0].get("type") == "video" else 1, t[0].get("name", "").lower()),
    )
    channels = [ch for ch, _ in combined]
    in_dms_flags = [d for _, d in combined]
    origins = ["DMS" if d else "available" for d in in_dms_flags]
    return channels, origins, list(in_dms_flags)


def apply_dms_selection(key: int, cursor: int, selected: list[bool], channels: list[dict]) -> None:
    """Mutate *selected* in-place for toggle / bulk-select keys."""
    if key == ord(" "):
        selected[cursor] = not selected[cursor]
        return
    bulk = {
        ord("a"): lambda _: True,
        ord("n"): lambda _: False,
        ord("v"): lambda ch: ch.get("type") == "video",
        ord("r"): lambda ch: ch.get("type") == "audio",
    }
    if key in bulk:
        predicate = bulk[key]
        for i, ch in enumerate(channels):
            selected[i] = predicate(ch)


def handle_dms_key(
    key: int, cursor: int, list_h: int, selected: list[bool], channels: list[dict]
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
    apply_dms_selection(key, cursor, selected, channels)
    return cursor, False, False


def dms_draw(
    stdscr,
    channels: list[dict],
    origins: list[str],
    selected: list[bool],
    cursor: int,
    scroll: int,
) -> None:
    """Render the full DMS editor screen."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    c_header = curses.color_pair(1)
    c_checked = curses.color_pair(2)
    c_unchecked = curses.color_pair(3)
    c_cursor = curses.color_pair(4)

    header = "  DMS Editor  |  SPACE=toggle  a=all  n=none  v=video  r=radio  ENTER=save  q=cancel  "
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
        ch_type = ch.get("type", "?")
        type_label = "TV" if ch_type == "video" else "R " if ch_type == "audio" else "? "
        freq = str(ch.get("frequency") or ch.get("freq", "?"))
        check = "[x]" if selected[idx] else "[ ]"
        line = f" {check} {type_label}  {ch.get('name', '?'):<34} {freq:>8} MHz  {origins[idx]:<7}"
        colour = c_cursor if idx == cursor else (c_checked if selected[idx] else c_unchecked)
        try:
            stdscr.addstr(2 + row, 0, line[: w - 1], colour)
        except curses.error:
            pass

    n_sel = sum(selected)
    n_tv = sum(1 for i, ch in enumerate(channels) if selected[i] and ch.get("type") == "video")
    n_radio = sum(1 for i, ch in enumerate(channels) if selected[i] and ch.get("type") == "audio")
    footer = f"  Selected: {n_sel}/{len(channels)}    TV: {n_tv}    Radio: {n_radio}    row {cursor + 1}/{len(channels)}  "
    try:
        stdscr.addstr(h - 2, 0, "─" * (w - 1), c_header)
        stdscr.addstr(h - 1, 0, footer[: w - 1], c_header)
    except curses.error:
        pass
    stdscr.refresh()


def interactive_dms_editor(dms_channels: list[dict], available: list[dict]) -> list[dict] | None:
    """Curses-based interactive DMS channel selector.

    Shows all channels (DMS ones pre-checked, available ones unchecked).
    Returns the new desired DMS channel list, or None if the user cancelled.

    Keys:
      ↑/↓ / j/k  navigate        Space  toggle
      a/n         select all / none      v/r  video / radio only
      Enter       save            q/Esc  cancel
    """
    channels, origins, selected = build_dms_entries(dms_channels, available)
    outcome: dict = {"result": None}

    def _run(stdscr):
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
            dms_draw(stdscr, channels, origins, selected, cursor, scroll)
            cursor, save, cancel = handle_dms_key(stdscr.getch(), cursor, list_h, selected, channels)
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
