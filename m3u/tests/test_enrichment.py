import pytest

from m3u.enrichment import EnrichmentResult, build_gracenote_lookups, enrich_m3u_text, normalize_name

# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------


def test_normalize_name_strips_spaces_and_punctuation():
    assert normalize_name("BBC One HD") == "bbconehd"


def test_normalize_name_lowercases():
    assert normalize_name("BBC1NWHD") == "bbc1nwhd"


def test_normalize_name_removes_non_alphanumeric():
    assert normalize_name("U&Dave") == "udave"
    assert normalize_name("4seven") == "4seven"


def test_normalize_name_empty_string():
    assert normalize_name("") == ""


# ---------------------------------------------------------------------------
# build_gracenote_lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def lookups():
    data = [
        {"callSign": "BBC1NWHD", "stationId": "10001", "name": "BBC One NW HD", "channel": "1"},
        {"callSign": "ITV1GHD", "stationId": "10002", "name": "ITV1 Granada HD", "channel": "2"},
        {"callSign": "", "name": "No Callsign", "stationId": "99", "channel": ""},  # should be skipped
    ]
    return build_gracenote_lookups(data)


def test_build_gracenote_lookups(lookups):
    callsign_to_info = lookups.callsign_to_info
    name_to_callsign = lookups.name_to_callsign
    assert "BBC1NWHD" in callsign_to_info
    assert callsign_to_info["BBC1NWHD"] == {
        "channel": "1",
        "stationId": "10001",
        "name": "BBC One NW HD",
    }
    assert "ITV1GHD" in callsign_to_info
    assert callsign_to_info["ITV1GHD"] == {
        "channel": "2",
        "stationId": "10002",
        "name": "ITV1 Granada HD",
    }
    assert len(callsign_to_info) == 2  # "No Callsign" entry is dropped

    assert name_to_callsign[normalize_name("BBC1NWHD")] == "BBC1NWHD"
    assert name_to_callsign[normalize_name("BBC One NW HD")] == "BBC1NWHD"
    assert name_to_callsign[normalize_name("ITV1GHD")] == "ITV1GHD"
    assert name_to_callsign[normalize_name("ITV1 Granada HD")] == "ITV1GHD"
    assert len(name_to_callsign) == 4


def test_build_gracenote_lookups_empty_data():
    lookups = build_gracenote_lookups([])
    assert lookups.callsign_to_info == {}
    assert lookups.name_to_callsign == {}


# ---------------------------------------------------------------------------
# enrich_m3u_text
# ---------------------------------------------------------------------------

M3U_INPUT = (
    "\n".join(
        [
            "#EXTM3U",
            "#EXTINF:-1,BBC1NWHD",
            "rtsp://192.168.1.1:554/?freq=10773",
            "#EXTINF:-1,ITV1GHD",
            "rtsp://192.168.1.1:554/?freq=11386",
            "#EXTINF:-1,UNKNOWNCHANNEL",
            "rtsp://192.168.1.1:554/?freq=99999",
            "#EXTINF:-1,BBCWAVE",  # This channel is not in GRACENOTE_DATA
            "rtsp://192.168.1.1:554/?freq=12345",
        ]
    )
    + "\n"
)


def test_enrich_m3u_text_returns_enrichment_result(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert isinstance(result, EnrichmentResult)


def test_enrich_m3u_text_counts(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert result.enriched == 2
    assert result.skipped == 2


def test_enrich_m3u_text_injects_channel_id_and_station_id(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    lines = result.text.splitlines()
    assert 'channel-id="BBC1NWHD"' in result.text
    assert 'tvc-guide-stationid="10001"' in result.text


def test_enrich_m3u_text_uses_gracenote_display_name_for_tvg_name(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert 'tvg-name="BBC One NW HD"' in result.text


def test_enrich_m3u_text_preserves_stream_url(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert "rtsp://192.168.1.1:554/?freq=10773" in result.text


def test_enrich_m3u_text_with_explicit_mappings(lookups):
    mappings = {"BBCWAVE": "BBC1NWHD"}
    result = enrich_m3u_text(M3U_INPUT, mappings, lookups)
    assert 'tvc-guide-stationid="10001"' in result.text


def test_enrich_m3u_text_explicit_mapping_overrides_lookup(lookups):
    # Map the unknown channel to a known callsign
    explicit = {"UNKNOWNCHANNEL": "ITV1GHD"}
    result = enrich_m3u_text(M3U_INPUT, explicit, lookups)
    assert result.enriched == 3
    assert result.skipped == 1  # BBCWAVE is still skipped


def test_enrich_m3u_text_skips_unmapped(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert "BBCWAVE" in result.skipped_channels
    assert "BBCWAVE" not in result.text


def test_enrich_m3u_text_skipped_channel_not_in_output(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert "UNKNOWNCHANNEL" not in result.text
    assert "99999" not in result.text


def test_enrich_m3u_text_skipped_channels_list(lookups):
    result = enrich_m3u_text(M3U_INPUT, {}, lookups)
    assert sorted(result.skipped_channels) == sorted(["UNKNOWNCHANNEL", "BBCWAVE"])


def test_enrich_m3u_text_skipped_channels_empty_when_all_matched(lookups):
    explicit = {"UNKNOWNCHANNEL": "ITV1GHD", "BBCWAVE": "BBC1NWHD"}
    result = enrich_m3u_text(M3U_INPUT, explicit, lookups)
    assert result.skipped_channels == []
