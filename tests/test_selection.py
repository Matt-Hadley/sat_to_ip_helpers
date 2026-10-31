import pytest

from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import Channel, ChannelRef, Lineup
from sat_to_ip.selection import Spec, match_specs, number_lineup, plan_selection, specs_from_config, timeshares

BBC = ChannelRef("k1", "BBC One HD", "tv", 10773.0)
BBC_SCOT = ChannelRef("k2", "BBC One HD", "tv", 10847.0)
ITV = ChannelRef("k3", "ITV1 HD", "tv", 10832.0)
R4 = ChannelRef("k4", "Radio 4", "radio", 11954.0)
ALL = [BBC, BBC_SCOT, ITV, R4]


def _specs(*names, start=None):
    block = {"names": list(names)}
    if start is not None:
        block["start"] = start
    return specs_from_config([block])


# specs_from_config ------------------------------------------------------------------------------


def test_blocks_become_specs_with_each_blocks_start_on_its_first_entry():
    specs = specs_from_config(
        [
            {"start": 101, "names": ["BBC One HD", {"name": "BBC One HD", "frequency": 10847}]},
            {"start": 801, "names": [{"rest": "radio"}]},
        ]
    )
    assert specs == [
        Spec("BBC One HD", "channels[0].names[0]", 101, name="BBC One HD"),
        Spec("BBC One HD@10847", "channels[0].names[1]", None, name="BBC One HD", frequency_mhz=10847.0),
        Spec("rest = radio", "channels[1].names[0]", 801, keyword="radio"),
    ]


def test_a_service_entry_matches_the_broadcast_service_whatever_its_name():
    renamed = ChannelRef("k9", "5DRAMA", "tv", 11305.0, "2105")
    other = ChannelRef("k8", "5SELECT", "tv", 11305.0, "2108")  # the old name on another service doesn't count
    specs = specs_from_config([{"names": [{"name": "5SELECT", "service": 2105}]}])
    assert specs[0].text == "5SELECT#2105" and specs[0].service == "2105"
    assert match_specs(specs, [other, renamed]) == [(specs[0], [renamed])]
    both = specs_from_config([{"names": [{"name": "x", "service": 2105, "frequency": 11306}]}])
    assert match_specs(both, [renamed])[0][1] == []  # with a frequency, both must match


def test_numbering_follows_service_entries_after_a_rename():
    lineup = Lineup((Channel("k1", "Great!Christmas", "tv", "u", service_id="53320"), Channel("k2", "B", "tv", "u")))
    specs = specs_from_config([{"start": 9035, "names": [{"name": "Great! Romance", "service": 53320}, "B"]}])
    assert [(c.number, c.name) for c in number_lineup(lineup, specs)] == [(9035, "Great!Christmas"), (9036, "B")]


def test_no_blocks_means_no_selection():
    assert specs_from_config(None) is None


def test_names_are_trimmed_and_a_block_may_have_no_start():
    assert _specs("  ITV1 HD ")[0] == Spec("ITV1 HD", "channels[0].names[0]", name="ITV1 HD")
    assert _specs({"name": " ITV1 HD"})[0] == Spec("ITV1 HD", "channels[0].names[0]", name="ITV1 HD")


@pytest.mark.parametrize(
    "blocks, message",
    [
        ([{"names": [{"name": "X", "service": "2105"}]}], "service: must be the broadcast service ID"),
        ([{"names": [{"name": "X", "service": -1}]}], "service: must be the broadcast service ID"),
        ([{"names": [{"name": "X", "service": True}]}], "service: must be the broadcast service ID"),
        ("BBC One HD", "must be \\[\\[channels\\]\\] blocks"),
        ([{"names": []}], "channels\\[0\\].names: required"),
        ([{"names": ["A"], "first": 1}], "channels\\[0\\]: unknown key\\(s\\) first"),
        ([{"start": 0, "names": ["A"]}], "channels\\[0\\].start: must be a channel number of 1 or more"),
        ([{"start": True, "names": ["A"]}], "must be a channel number"),
        ([{"names": [" "]}], "channels\\[0\\].names\\[0\\]: must not be empty"),
        ([{"names": [5]}], "must be a channel name or a table"),
        ([{"names": [{"name": "A", "freq": 1}]}], "names\\[0\\]: unknown key\\(s\\) freq"),
        ([{"names": [{"frequency": 1}]}], "names\\[0\\].name: required"),
        ([{"names": [{"name": "A", "frequency": "x"}]}], "names\\[0\\].frequency: must be a number"),
        ([{"names": [{"name": "A", "additional_names": "B"}]}], "additional_names: must be a list of channel names"),
        ([{"names": [{"name": "A", "additional_names": [" "]}]}], "additional_names: must be a list of channel names"),
        ([{"names": [{"name": "A", "extra_pids": 264}]}], "extra_pids: must be a list of PIDs"),
        (
            [{"names": [{"name": "A", "service": 1, "additional_names": ["B"]}]}],
            "names\\[0\\]: remove service; a timeshare",
        ),
        ([{"names": [{"name": "A", "extra_pids": [8192]}]}], "extra_pids: must be a list of PIDs"),
        ([{"names": [{"name": "A", "extra_pids": [True]}]}], "extra_pids: must be a list of PIDs"),
        (
            [{"start": 10, "names": [{"name": "A", "additional_names": ["B"]}]}, {"start": 11, "names": ["C"]}],
            "channels\\[1\\].start: 11 overlaps channels\\[0\\] \\(its channels run up to 11\\)",
        ),
        ([{"names": [{"rest": "films"}]}], 'use { rest = "tv" }'),
        ([{"names": [{"rest": "tv", "name": "A"}]}], 'use { rest = "tv" }'),
        (
            [{"start": 10, "names": ["A", "B"]}, {"start": 11, "names": ["C"]}],
            "channels\\[1\\].start: 11 overlaps channels\\[0\\] \\(its channels run up to 11\\)",
        ),
    ],
)
def test_bad_blocks_name_the_key(blocks, message):
    with pytest.raises(ConfigError, match=message):
        specs_from_config(blocks)


def test_a_timeshare_entry_matches_any_of_its_names():
    spec = specs_from_config([{"names": [{"name": "BBC Three HD", "additional_names": [" CBBC HD"]}]}])[0]
    assert spec.text == "BBC Three HD/CBBC HD" and spec.names == ("BBC Three HD", "CBBC HD")
    assert [spec.matches(ChannelRef("k", n)) for n in ("cbbc hd", "BBC Three HD", "CBBC")] == [True, True, False]
    assert timeshares([spec, *_specs("A")]) == [frozenset({"bbc three hd", "cbbc hd"})]


def test_rest_entries_leave_room_checks_to_run_time():
    assert len(specs_from_config([{"names": [{"rest": "tv"}]}, {"start": 2, "names": ["Radio 4"]}])) == 2


# match_specs / plan_selection --------------------------------------------------------------------


def _match(*entries):
    return [(s.text, [c.key for c in found]) for s, found in match_specs(_specs(*entries), ALL)]


def test_an_entry_with_a_frequency_or_service_chooses_before_a_plain_name():
    entries = ["BBC One HD", {"name": "BBC One HD", "frequency": 10773}, {"name": "x", "service": 7}, "ITV1 HD"]
    channels = [BBC, BBC_SCOT, ChannelRef("k5", "ITV1 HD", "tv", 11000.0, "7"), ITV]
    found = [(s.text, [c.key for c in f]) for s, f in match_specs(_specs(*entries), channels)]
    assert found == [("BBC One HD", ["k2"]), ("BBC One HD@10773", ["k1"]), ("x#7", ["k5"]), ("ITV1 HD", ["k3"])]


def test_rest_takes_only_what_no_other_entry_lists_wherever_it_is():
    found = [(s.text, [c.key for c in f]) for s, f in match_specs(_specs({"rest": "tv"}, "ITV1 HD"), ALL)]
    assert found == [("rest = tv", ["k1", "k2"]), ("ITV1 HD", ["k3"])]


def test_a_name_picks_the_same_channel_whatever_order_the_receiver_lists_them():
    for channels in ([BBC, BBC_SCOT], [BBC_SCOT, BBC]):
        assert [[c.key for c in f] for _, f in match_specs(_specs("BBC One HD", {"rest": "tv"}), channels)] == [
            ["k1"],
            ["k2"],
        ]


def test_a_retuned_channel_keeps_its_entry_over_another_region_on_a_lower_frequency():
    moved, other = ChannelRef("k9", "ITV1 HD", "tv", 11222.0), ChannelRef("k8", "ITV1 HD", "tv", 11097.0)
    assert [c.key for c in plan_selection([moved], [other], _specs("ITV1 HD")).proposed] == ["k9"]


def test_the_saved_order_records_which_region_a_name_chose_through_moves_and_new_regions():
    mine, other = ChannelRef("k9", "ITV1 HD", "tv", 11222.0), ChannelRef("k8", "ITV1 HD", "tv", 10758.0)
    specs = specs_from_config([{"start": 1, "names": [{"rest": "tv"}]}, {"start": 103, "names": ["ITV1 HD"]}])
    plan = plan_selection([mine], [other], specs)  # a new region on a lower frequency
    assert [c.key for c in plan.proposed] == ["k9", "k8"]  # the name's choice before rest's
    for _ in range(2):  # and it stays put, publish included
        plan = plan_selection(plan.proposed, [], specs)
        assert [c.key for c in plan.proposed] == ["k9", "k8"]
    lineup = Lineup(tuple(Channel(c.key, c.name, c.kind, "u", 0, c.frequency_mhz) for c in plan.proposed))
    assert _numbers(number_lineup(lineup, specs)) == [(1, "ITV1 HD"), (103, "ITV1 HD")]
    assert [c.key for c in number_lineup(lineup, specs)] == ["k8", "k9"]


def test_name_takes_the_first_match_case_insensitively():
    assert _match("bbc one hd", "BBC One HD") == [("bbc one hd", ["k1"]), ("BBC One HD", ["k2"])]


def test_name_with_frequency_picks_the_right_one_within_tolerance():
    assert _match({"name": "BBC One HD", "frequency": 10847.25}) == [("BBC One HD@10847.2", ["k2"])]
    assert _match({"name": "BBC One HD", "frequency": 11000}) == [("BBC One HD@11000", [])]


def test_an_entry_takes_one_channel_even_when_name_and_frequency_repeat():
    twins = [ChannelRef("a", "BBC One NI HD", "tv", 10847.0), ChannelRef("b", "BBC One NI HD", "tv", 10847.0)]
    once = specs_from_config([{"names": [{"name": "BBC One NI HD", "frequency": 10847}]}])
    twice = specs_from_config([{"names": [{"name": "BBC One NI HD", "frequency": 10847}] * 2}])
    assert [[c.key for c in found] for _, found in match_specs(once, twins)] == [["a"]]
    assert [[c.key for c in found] for _, found in match_specs(twice, twins)] == [["a"], ["b"]]


def test_rest_takes_what_is_left_of_its_kind():
    assert _match("ITV1 HD", {"rest": "tv"}, {"rest": "radio"}, {"rest": "all"}) == [
        ("ITV1 HD", ["k3"]),
        ("rest = tv", ["k1", "k2"]),
        ("rest = radio", ["k4"]),
        ("rest = all", []),
    ]


def test_frequency_entry_skips_channels_without_a_frequency():
    assert match_specs(_specs({"name": "X", "frequency": 100}), [ChannelRef("a", "X")])[0][1] == []


def test_plan_selection():
    plan = plan_selection([BBC, ITV], [BBC_SCOT, R4], _specs("Radio 4", "ITV1 HD", "Gone HD", {"rest": "tv"}))
    assert [c.key for c in plan.proposed] == ["k4", "k3", "k1", "k2"]
    assert plan.current == (BBC, ITV) and plan.unmatched_specs == ("Gone HD",)


# number_lineup ----------------------------------------------------------------------------------


def _lineup(*names):
    return Lineup(tuple(Channel(f"k-{n}", n, "radio" if n.startswith("R") else "tv", "u") for n in names))


def _numbers(lineup):
    return [(c.number, c.name) for c in lineup]


def test_without_blocks_numbers_follow_the_receiver_order():
    assert _numbers(number_lineup(_lineup("B", "A"), None)) == [(1, "B"), (2, "A")]


def test_blocks_count_up_from_their_start_and_sort_the_lineup():
    specs = specs_from_config([{"start": 101, "names": ["A", "B"]}, {"start": 801, "names": ["R1", "R2"]}])
    lineup = number_lineup(_lineup("R2", "B", "R1", "A"), specs)
    assert _numbers(lineup) == [(101, "A"), (102, "B"), (801, "R1"), (802, "R2")]


def test_a_missing_channel_keeps_its_number_free():
    assert _numbers(number_lineup(_lineup("B", "C"), _specs("Gone", "B", "C"))) == [(2, "B"), (3, "C")]


def test_unlisted_channels_come_after_the_listed_ones():
    assert _numbers(number_lineup(_lineup("X", "A", "Y"), _specs("A", start=10))) == [(10, "A"), (11, "X"), (12, "Y")]


def test_a_timeshare_is_published_once_per_name_whatever_is_on_air():
    specs = specs_from_config([{"start": 9, "names": [{"name": "BBC Three HD", "additional_names": ["CBBC HD"]}, "B"]}])
    lineup = number_lineup(_lineup("B", "CBBC HD"), specs)
    assert [(c.number, c.name, c.key, c.stream_url) for c in lineup] == [
        (9, "BBC Three HD", "k-CBBC HD", "u"),
        (10, "CBBC HD", "k-CBBC HD/CBBC HD", "u"),
        (11, "B", "k-B", "u"),
    ]


def test_extra_pids_are_added_to_the_stream_of_every_name():
    entry = {"name": "BBC Three HD", "additional_names": ["CBBC HD"], "extra_pids": [264, 268]}
    url = "rtsp://box:554/?src=1&freq=10847&pids=0,16,268,5600"
    lineup = Lineup((Channel("k", "BBC Three HD", "tv", url),))
    assert {c.stream_url for c in number_lineup(lineup, specs_from_config([{"names": [entry]}]))} == {
        "rtsp://box:554/?src=1&freq=10847&pids=0,16,268,5600,264"
    }


@pytest.mark.parametrize("url", ["rtsp://box:554/?src=1&pids=all", "rtsp://box:554/?src=1", "http://box/ch.ts"])
def test_extra_pids_leave_a_stream_without_a_pids_list_alone(url):
    specs = specs_from_config([{"names": [{"name": "A", "extra_pids": [264]}]}])
    assert number_lineup(Lineup((Channel("k", "A", "tv", url),)), specs).channels[0].stream_url == url


def test_a_missing_timeshare_keeps_a_number_free_for_each_name():
    specs = specs_from_config([{"names": [{"name": "BBC Four HD", "additional_names": ["CBeebies HD"]}, "B"]}])
    assert _numbers(number_lineup(_lineup("B"), specs)) == [(3, "B")]


def test_rest_overflowing_into_the_next_block_is_a_check_error():
    specs = specs_from_config([{"names": [{"rest": "tv"}]}, {"start": 2, "names": ["R1"]}])
    with pytest.raises(CheckError, match="channels\\[1\\].names\\[0\\]: start 2 is already taken"):
        number_lineup(_lineup("A", "B", "R1"), specs)
