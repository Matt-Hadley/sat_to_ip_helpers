from sat_to_ip.octopus.selection import channel_key, filter_channels, match_entry


def test_match_entry_by_name():
    channels = [{"name": "BBC One HD", "serviceid": "1", "frequency": 10773}]
    seen_ids = set()
    matches = match_entry("BBC One HD", channels, seen_ids)
    assert matches == [channels[0]]


def test_match_entry_by_name_case_insensitive():
    channels = [{"name": "BBC One HD", "serviceid": "1", "frequency": 10773}]
    seen_ids = set()
    matches = match_entry("bbc one hd", channels, seen_ids)
    assert matches == [channels[0]]


def test_match_entry_by_name_and_frequency():
    channels = [
        {"name": "BBC One HD", "serviceid": "1", "frequency": 10773},
        {"name": "BBC One HD", "serviceid": "2", "frequency": 11386},
    ]
    seen_ids = set()
    matches = match_entry("BBC One HD@11386", channels, seen_ids)
    assert matches == [channels[1]]


def test_match_entry_skips_seen():
    channels = [{"name": "BBC One HD", "serviceid": "1", "frequency": 10773}]
    seen_ids = {"octopus:1"}
    matches = match_entry("BBC One HD", channels, seen_ids)
    assert matches == []


def test_filter_channels_all():
    channels = [{"name": "A"}, {"name": "B"}]
    matched, unmatched = filter_channels(channels, ["all"])
    assert matched == channels
    assert unmatched == []


def test_filter_channels_video():
    channels = [
        {"name": "V", "type": "video"},
        {"name": "A", "type": "audio"},
    ]
    matched, unmatched = filter_channels(channels, ["video"])
    assert matched == [channels[0]]
    assert unmatched == []


def test_filter_channels_names():
    channels = [
        {"name": "BBC One HD", "serviceid": "1", "frequency": 10773},
        {"name": "ITV HD", "serviceid": "2", "frequency": 11386},
    ]
    matched, unmatched = filter_channels(channels, ["BBC One HD", "ITV HD", "Missing Channel"])
    assert len(matched) == 2
    assert matched[0]["name"] == "BBC One HD"
    assert matched[1]["name"] == "ITV HD"
    assert unmatched == ["Missing Channel"]


def test_filter_channels_keywords_and_names_never_repeat_a_channel():
    channels = [
        {"name": "A", "serviceid": "1", "type": "video"},
        {"name": "R", "serviceid": "2", "type": "audio"},
        {"name": "B", "serviceid": "3", "type": "video"},
    ]
    matched, unmatched = filter_channels(channels, ["R", "tv", "radio", "all", "A"])
    assert [c["name"] for c in matched] == ["R", "A", "B"]
    assert unmatched == ["A"]  # already taken by "tv"


def test_filter_channels_audio_alias():
    channels = [{"name": "V", "type": "video"}, {"name": "A", "type": "audio"}]
    assert filter_channels(channels, ["audio"]) == ([channels[1]], [])


def test_channel_key_falls_back_to_name_and_frequency():
    assert channel_key({"serviceid": 10301, "name": "X"}) == "octopus:10301"
    assert channel_key({"name": "X", "freq": 10773}) == "octopus:X@10773"
    assert channel_key({"name": "X", "serviceid": "", "frequency": 1}) == "octopus:X@1"
