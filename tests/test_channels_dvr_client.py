from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip.channels_dvr.client import ChannelsDVRClient
from sat_to_ip.exceptions import IntegrationError


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


def test_get_gracenote_http_error_is_integration_error():
    client, session = _client()
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    with pytest.raises(IntegrationError, match="can't fetch station list 'X'.*404"):
        client.get_gracenote("X")


def test_get_gracenote_connection_error_is_integration_error():
    client, session = _client()
    session.get.side_effect = requests.ConnectionError("refused")
    with pytest.raises(IntegrationError, match="refused"):
        client.get_gracenote("X")


def test_get_gracenote_bad_json_is_integration_error():
    client, session = _client()
    session.get.return_value.json.side_effect = ValueError("Expecting value")
    with pytest.raises(IntegrationError, match="not JSON"):
        client.get_gracenote("X")


@pytest.mark.parametrize("data", [{"error": "nope"}, ["BBC1NWHD"], None])
def test_get_gracenote_unexpected_shape_is_integration_error(data):
    client, session = _client()
    session.get.return_value.json.return_value = data
    with pytest.raises(IntegrationError, match="not a list of stations"):
        client.get_gracenote("X")


# ---------------------------------------------------------------------------
# M3U sources
# ---------------------------------------------------------------------------

SETTINGS = {"name": "SatTV", "source": "Text", "text": "#EXTM3U\n", "limit": "4", "start_number": "500"}


def test_get_m3u_source_returns_settings():
    client, session = _client()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = SETTINGS
    assert client.get_m3u_source("SatTV") == SETTINGS
    assert session.get.call_args[0][0] == "http://dvr:8089/providers/m3u/sources/SatTV"


def test_get_m3u_source_missing_says_create_it_first():
    client, session = _client()
    session.get.return_value.status_code = 404
    with pytest.raises(IntegrationError, match="source 'SatTV' doesn't exist; create it in Channels DVR first"):
        client.get_m3u_source("SatTV")


def test_get_m3u_source_http_error_is_integration_error():
    client, session = _client()
    session.get.side_effect = requests.ConnectionError("refused")
    with pytest.raises(IntegrationError, match="can't read source 'SatTV'.*refused"):
        client.get_m3u_source("SatTV")


def test_get_m3u_source_bad_json_is_integration_error():
    client, session = _client()
    session.get.return_value.json.side_effect = ValueError("Expecting value")
    with pytest.raises(IntegrationError, match="not JSON"):
        client.get_m3u_source("SatTV")


def test_get_m3u_source_unexpected_shape_is_integration_error():
    client, session = _client()
    session.get.return_value.json.return_value = ["nope"]
    with pytest.raises(IntegrationError, match="not a settings object"):
        client.get_m3u_source("SatTV")


def test_update_m3u_source_puts_exactly_the_given_settings():
    client, session = _client()
    client.update_m3u_source("SatTV", SETTINGS)
    assert session.put.call_args[0][0] == "http://dvr:8089/providers/m3u/sources/SatTV"
    assert session.put.call_args.kwargs["json"] == SETTINGS


def test_update_m3u_source_http_error_is_integration_error():
    client, session = _client()
    session.put.return_value.raise_for_status.side_effect = requests.HTTPError("500")
    with pytest.raises(IntegrationError, match="can't update source 'src'"):
        client.update_m3u_source("src", SETTINGS)


def test_refresh_m3u_source_posts_refresh():
    client, session = _client()
    client.refresh_m3u_source("SatTV")
    session.post.assert_called_once_with("http://dvr:8089/providers/m3u/sources/SatTV/refresh")


def test_refresh_m3u_source_http_error_is_integration_error():
    client, session = _client()
    session.post.return_value.raise_for_status.side_effect = requests.HTTPError("500")
    with pytest.raises(IntegrationError, match="can't refresh source 'src'.*500"):
        client.refresh_m3u_source("src")


# ---------------------------------------------------------------------------
# connect classmethod
# ---------------------------------------------------------------------------


def test_connect_keeps_base_url_and_uses_shared_session():
    with patch("sat_to_ip.http.session", return_value=requests.Session()) as make:
        client = ChannelsDVRClient.connect("http://myserver:8089/", "abc123")
    make.assert_called_once_with()
    assert client.base_url == "http://myserver:8089"


def test_connect_sets_sid_cookie():
    client = ChannelsDVRClient.connect("http://myserver:8089", "abc123")
    assert client.session.cookies.get("SID") == "abc123"
    assert client.session.get_adapter("http://myserver:8089").timeout == 30


def test_connect_without_sid_sets_no_cookie():
    client = ChannelsDVRClient.connect("http://myserver:8089")
    assert "SID" not in client.session.cookies
