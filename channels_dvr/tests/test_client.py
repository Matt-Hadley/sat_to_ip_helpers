from unittest.mock import MagicMock

from channels_dvr.client import ChannelsDVRClient


def _client(base_url: str = "http://dvr:8089") -> tuple[ChannelsDVRClient, MagicMock]:
    session = MagicMock()
    return ChannelsDVRClient(base_url, session), session


# ---------------------------------------------------------------------------
# get_gracenote
# ---------------------------------------------------------------------------


def test_get_gracenote_calls_correct_endpoint():
    client, session = _client()
    session.get.return_value.json.return_value = []
    client.get_gracenote("GBR-1000193-DEFAULT")
    url = session.get.call_args[0][0]
    assert url == "http://dvr:8089/dvr/guide/stations/GBR-1000193-DEFAULT"


def test_get_gracenote_returns_list():
    client, session = _client()
    data = [{"callSign": "BBC1NWHD", "stationId": "10001"}]
    session.get.return_value.json.return_value = data
    result = client.get_gracenote("GBR-1000193-DEFAULT")
    assert result == data


def test_get_gracenote_unwraps_outer_list():
    client, session = _client()
    inner = [{"callSign": "BBC1NWHD"}]
    session.get.return_value.json.return_value = [inner]  # nested
    result = client.get_gracenote("GBR-1000193-DEFAULT")
    assert result == inner


# ---------------------------------------------------------------------------
# update_m3u_source
# ---------------------------------------------------------------------------


def test_update_m3u_source_puts_to_correct_endpoint():
    client, session = _client()
    client.update_m3u_source("MySource123", "My Source", "#EXTM3U\n")
    url = session.put.call_args[0][0]
    assert url == "http://dvr:8089/providers/m3u/sources/MySource123"


def test_update_m3u_source_payload_shape():
    client, session = _client()
    m3u = "#EXTM3U\n#EXTINF:-1,BBC1NWHD\nrtsp://...\n"
    client.update_m3u_source("MySource123", "My Source", m3u)
    payload = session.put.call_args.kwargs["json"]
    assert payload["name"] == "My Source"
    assert payload["text"] == m3u
    assert payload["type"] == "MPEG-TS"
    assert payload["source"] == "Text"


def test_update_m3u_source_default_options():
    client, session = _client()
    client.update_m3u_source("src", "Source", "#EXTM3U\n")
    payload = session.put.call_args.kwargs["json"]
    assert payload["refresh"] == "24"
    assert payload["limit"] == "8"
    assert payload["numbering"] == "ignore"
    assert payload["logos"] == "guide"


def test_update_m3u_source_custom_options():
    client, session = _client()
    client.update_m3u_source("src", "Source", "#EXTM3U\n", refresh="12", limit="4")
    payload = session.put.call_args.kwargs["json"]
    assert payload["refresh"] == "12"
    assert payload["limit"] == "4"


# ---------------------------------------------------------------------------
# with_sid classmethod
# ---------------------------------------------------------------------------


def test_with_sid_sets_correct_base_url():
    client = ChannelsDVRClient.with_sid("myserver", 8089, "abc123")
    assert client.base_url == "http://myserver:8089"


def test_with_sid_sets_sid_cookie():
    client = ChannelsDVRClient.with_sid("myserver", 8089, "abc123")
    assert client.session.cookies.get("SID") == "abc123"
