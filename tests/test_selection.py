import pytest

from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import Channel, ChannelRef, Lineup
from sat_to_ip.selection import Spec, match_specs, number_lineup, parse_spec, plan_selection, read_spec_file

BBC = ChannelRef("k1", "BBC One HD", "tv", 10773.0)
BBC_SCOT = ChannelRef("k2", "BBC One HD", "tv", 10847.0)
ITV = ChannelRef("k3", "ITV1 HD", "tv", 10832.0)
R4 = ChannelRef("k4", "Radio 4", "radio", 11954.0)
ALL = [BBC, BBC_SCOT, ITV, R4]


# parse_spec / read_spec_file ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("BBC One HD", Spec("BBC One HD", name="BBC One HD")),
        ("  BBC One HD@10773 ", Spec("BBC One HD@10773", name="BBC One HD", frequency_mhz=10773.0)),
        ("TV", Spec("TV", keyword="tv")),
        ("101 = BBC One HD", Spec("101 = BBC One HD", number=101, name="BBC One HD")),
        ("801=radio", Spec("801=radio", number=801, keyword="radio")),
        ("5 USA", Spec("5 USA", name="5 USA")),  # a name starting with a number isn't a number
        ("12 = 5 USA", Spec("12 = 5 USA", number=12, name="5 USA")),
    ],
)
def test_parse_spec(text, expected):
    assert parse_spec(text) == expected


@pytest.mark.parametrize(
    "text, message",
    [
        ("0 = BBC One HD", "line 3: channel numbers start at 1"),
        ("101 =", "has a number but no channel"),
        ("BBC One HD@soon", "must be a frequency in MHz"),
        ("@10773", "missing the channel name"),
    ],
)
def test_parse_spec_errors_name_the_line(text, message):
    with pytest.raises(ConfigError, match=message):
        parse_spec(text, 3)


def test_read_spec_file(tmp_path):
    path = tmp_path / "channels.txt"
    path.write_text("# TV\n101 = BBC One HD\n\nITV1 HD\n  # radio\n801 = radio\n")
    assert [(s.line, s.number, s.name or s.keyword) for s in read_spec_file(path)] == [
        (2, 101, "BBC One HD"),
        (4, None, "ITV1 HD"),
        (6, 801, "radio"),
    ]


@pytest.mark.parametrize("content", ["10 = A\n10 = B\n", "10 = A\nB\n11 = C\n", "20 = A\n5 = B\n"])
def test_read_spec_file_numbers_must_go_up(tmp_path, content):
    path = tmp_path / "channels.txt"
    path.write_text(content)
    with pytest.raises(ConfigError, match="numbers must go up"):
        read_spec_file(path)


def test_read_spec_file_allows_room_after_keywords(tmp_path):
    path = tmp_path / "channels.txt"
    path.write_text("tv\n2 = Radio 4\n")  # a keyword's size is only known at run time
    assert len(read_spec_file(path)) == 2


def test_read_spec_file_unreadable(tmp_path):
    with pytest.raises(ConfigError, match="Can't read channel list"):
        read_spec_file(tmp_path / "missing.txt")


# match_specs / plan_selection --------------------------------------------------------------------


def _match(*texts):
    return [(s.text, [c.key for c in found]) for s, found in match_specs([parse_spec(t) for t in texts], ALL)]


def test_name_takes_the_first_match_case_insensitively():
    assert _match("bbc one hd", "BBC One HD") == [("bbc one hd", ["k1"]), ("BBC One HD", ["k2"])]


def test_name_at_frequency_picks_the_right_one_within_tolerance():
    assert _match("BBC One HD@10847.25") == [("BBC One HD@10847.25", ["k2"])]
    assert _match("BBC One HD@11000") == [("BBC One HD@11000", [])]


def test_keywords_take_what_is_left_of_their_kind():
    assert _match("ITV1 HD", "tv", "radio", "all") == [
        ("ITV1 HD", ["k3"]),
        ("tv", ["k1", "k2"]),
        ("radio", ["k4"]),
        ("all", []),
    ]


def test_frequency_spec_skips_channels_without_a_frequency():
    found = match_specs([parse_spec("X@100")], [ChannelRef("a", "X")])
    assert found[0][1] == []


def test_plan_selection():
    specs = [parse_spec(t) for t in ("Radio 4", "ITV1 HD", "Gone HD", "tv")]
    plan = plan_selection([BBC, ITV], [BBC_SCOT, R4], specs)
    assert [c.key for c in plan.proposed] == ["k4", "k3", "k1", "k2"]
    assert plan.current == (BBC, ITV) and plan.unmatched_specs == ("Gone HD",)


# number_lineup ----------------------------------------------------------------------------------


def _lineup(*names):
    return Lineup(tuple(Channel(f"k-{n}", n, "radio" if n.startswith("R") else "tv", "u") for n in names))


def _numbers(lineup):
    return [(c.number, c.name) for c in lineup]


def test_without_a_list_numbers_follow_the_receiver_order():
    assert _numbers(number_lineup(_lineup("B", "A"), None)) == [(1, "B"), (2, "A")]


def test_numbers_count_up_from_explicit_ones_and_sort_the_lineup():
    specs = [parse_spec(t) for t in ("101 = A", "B", "801 = R1", "R2")]
    lineup = number_lineup(_lineup("R2", "B", "R1", "A"), specs)
    assert _numbers(lineup) == [(101, "A"), (102, "B"), (801, "R1"), (802, "R2")]


def test_a_missing_channel_keeps_its_number_free():
    specs = [parse_spec(t) for t in ("Gone", "B", "C")]
    assert _numbers(number_lineup(_lineup("B", "C"), specs)) == [(2, "B"), (3, "C")]


def test_unlisted_channels_come_after_the_listed_ones():
    specs = [parse_spec(t) for t in ("10 = A",)]
    assert _numbers(number_lineup(_lineup("X", "A", "Y"), specs)) == [(10, "A"), (11, "X"), (12, "Y")]


def test_keyword_then_explicit_number_collision_is_a_check_error():
    specs = [parse_spec("tv"), parse_spec("2 = R1")]
    specs[1] = Spec(specs[1].text, 7, 2, name="R1")
    with pytest.raises(CheckError, match="Channel list line 7: number 2 is already taken"):
        number_lineup(_lineup("A", "B", "R1"), specs)
