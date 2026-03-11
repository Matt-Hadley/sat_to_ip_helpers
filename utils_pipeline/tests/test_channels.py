from utils_pipeline.channels import filter_channels, match_entry

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
    seen_ids = {"1"}
    matches = match_entry("BBC One HD", channels, seen_ids)
    assert matches == []

def test_filter_channels_all():
    channels = [{"name": "A"}, {"name": "B"}]
    matched, unmatched = filter_channels(channels, "all")
    assert matched == channels
    assert unmatched == []

def test_filter_channels_video():
    channels = [
        {"name": "V", "type": "video"},
        {"name": "A", "type": "audio"},
    ]
    matched, unmatched = filter_channels(channels, "video")
    assert matched == [channels[0]]
    assert unmatched == []

def test_filter_channels_comma_spec():
    channels = [
        {"name": "BBC One HD", "serviceid": "1", "frequency": 10773},
        {"name": "ITV HD", "serviceid": "2", "frequency": 11386},
    ]
    spec = "BBC One HD, ITV HD, Missing Channel"
    matched, unmatched = filter_channels(channels, spec)
    assert len(matched) == 2
    assert matched[0]["name"] == "BBC One HD"
    assert matched[1]["name"] == "ITV HD"
    assert unmatched == ["Missing Channel"]
