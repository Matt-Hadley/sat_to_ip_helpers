import curses
from unittest.mock import DEFAULT, MagicMock, patch

import pytest

from sat_to_ip.channel_picker import apply_selection_key, build_entries, draw, handle_key, pick
from sat_to_ip.model import ChannelRef

CHANNELS = [
    ChannelRef("k1", "BBC One HD", "tv", 10773.0),
    ChannelRef("k2", "Radio 4", "radio", 11954.25),
    ChannelRef("k3", "Mystery"),
]


@pytest.fixture
def no_curses_init():
    """Stub the curses calls that need a real terminal."""
    with patch.multiple(
        "sat_to_ip.channel_picker.curses",
        curs_set=DEFAULT,
        start_color=DEFAULT,
        use_default_colors=DEFAULT,
        init_pair=DEFAULT,
        color_pair=MagicMock(return_value=0),
    ):
        yield


def _screen(keys=(), size=(10, 80)):
    stdscr = MagicMock()
    stdscr.getmaxyx.return_value = size
    stdscr.getch.side_effect = list(keys)
    return stdscr


@pytest.mark.parametrize(
    "key, expected",
    [
        (ord("a"), [True, True, True]),
        (ord("n"), [False, False, False]),
        (ord("t"), [True, False, True]),
        (ord("r"), [False, True, False]),
        (ord("x"), [False, False, True]),  # unknown key leaves selection alone
    ],
)
def test_apply_selection_bulk_keys(key, expected):
    selected = [False, False, True]
    apply_selection_key(key, 0, selected, CHANNELS)
    assert selected == expected


def test_apply_selection_space_toggles_cursor_row():
    selected = [False, False, False]
    apply_selection_key(ord(" "), 1, selected, CHANNELS)
    assert selected == [False, True, False]


@pytest.mark.parametrize(
    "key, cursor, expected",
    [
        (ord("q"), 1, (1, False, True)),
        (27, 1, (1, False, True)),
        (10, 1, (1, True, False)),
        (curses.KEY_ENTER, 1, (1, True, False)),
        (curses.KEY_UP, 0, (0, False, False)),
        (ord("k"), 2, (1, False, False)),
        (curses.KEY_DOWN, 2, (2, False, False)),
        (ord("j"), 0, (1, False, False)),
        (curses.KEY_PPAGE, 2, (0, False, False)),
        (curses.KEY_NPAGE, 0, (2, False, False)),
        (ord(" "), 0, (0, False, False)),
    ],
)
def test_handle_key(key, cursor, expected):
    assert handle_key(key, cursor, 5, [False] * 3, CHANNELS) == expected


def test_draw_renders_rows_and_footer(no_curses_init):
    stdscr = _screen()
    draw(stdscr, CHANNELS, ["saved", "found", "found"], [True, True, False], 1, 0)
    text = "\n".join(str(c.args[2]) for c in stdscr.addstr.call_args_list)
    assert "[x] TV  BBC One HD" in text and "10773 MHz  saved" in text
    assert "R   Radio 4" in text and "11954.2 MHz" in text
    assert "[ ] TV  Mystery" in text and "? MHz" in text
    assert "Selected: 2/3    TV: 1    Radio: 1" in text
    stdscr.refresh.assert_called_once()


def test_draw_tolerates_tiny_terminal(no_curses_init):
    stdscr = _screen(size=(5, 20))
    # Writing to the last cell of a curses window raises; every write after the header must survive that
    stdscr.addstr.side_effect = [None] + [curses.error] * 10
    draw(stdscr, CHANNELS, ["saved"] * 3, [False] * 3, 0, 0)


def _run_editor(stdscr, saved, available):
    with patch("sat_to_ip.channel_picker.curses.wrapper", side_effect=lambda fn: fn(stdscr)):
        return pick(saved, available)


def test_editor_saves_selection(no_curses_init):
    dms = [CHANNELS[0]]
    available = [CHANNELS[1]]
    # Down to Radio 4, tick it, save
    stdscr = _screen([curses.KEY_DOWN, ord(" "), 10])
    assert _run_editor(stdscr, dms, available) == [CHANNELS[0], CHANNELS[1]]


def test_editor_scrolls_when_cursor_passes_bottom(no_curses_init):
    available = [ChannelRef(f"k{i}", f"Ch {i}") for i in range(10)]
    # 6-row screen leaves 2 list rows; walking down 3 times forces a scroll
    stdscr = _screen([curses.KEY_DOWN] * 3 + [ord(" "), 10], size=(6, 80))
    assert _run_editor(stdscr, [], available) == [available[3]]


def test_editor_cancel_returns_none(no_curses_init):
    assert _run_editor(_screen([ord("q")]), [CHANNELS[0]], []) is None


def test_editor_ctrl_c_returns_none():
    with patch("sat_to_ip.channel_picker.curses.wrapper", side_effect=KeyboardInterrupt):
        assert pick([], CHANNELS) is None


def test_build_entries_ticks_saved_and_sorts_tv_first():
    saved = [ChannelRef("r", "Radio 1", "radio")]
    available = [ChannelRef("b", "BBC One HD")]
    channels, origins, selected = build_entries(saved, available)
    assert [c.name for c in channels] == ["BBC One HD", "Radio 1"]
    assert origins == ["found", "saved"] and selected == [False, True]
