from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip.octopus.client import OctopusClient


def _client(base_url: str = "http://octopus") -> tuple[OctopusClient, MagicMock]:
    session = MagicMock()
    return OctopusClient(base_url, session), session


# ---------------------------------------------------------------------------
# start_scan — position normalisation and tuner-slot padding
# ---------------------------------------------------------------------------


def test_start_scan_normalises_dot_in_position():
    client, session = _client()
    client.start_scan(["28.2E"])
    payload = session.post.call_args.kwargs["json"]
    assert payload["target-s"][0] == "282E"


def test_start_scan_pads_to_four_slots():
    client, session = _client()
    client.start_scan(["28.2E"])
    payload = session.post.call_args.kwargs["json"]
    assert payload["target-s"] == ["282E", "disabled", "disabled", "disabled"]


def test_start_scan_multiple_positions():
    client, session = _client()
    client.start_scan(["28.2E", "13.0E"])
    payload = session.post.call_args.kwargs["json"]
    assert payload["target-s"] == ["282E", "130E", "disabled", "disabled"]


def test_start_scan_posts_to_correct_endpoint():
    client, session = _client()
    client.start_scan(["28.2E"])
    url = session.post.call_args[0][0]
    assert url == "http://octopus/startsearch-satip"


# ---------------------------------------------------------------------------
# get_dms_channels — response unwrapping
# ---------------------------------------------------------------------------


def test_get_dms_channels_returns_list_directly():
    client, session = _client()
    session.post.return_value.json.return_value = [{"serviceid": "123"}]
    result = client.get_dms_channels()
    assert result == [{"serviceid": "123"}]


def test_get_dms_channels_unwraps_data_key():
    client, session = _client()
    session.post.return_value.json.return_value = {"data": [{"serviceid": "456"}], "total": 1}
    result = client.get_dms_channels()
    assert result == [{"serviceid": "456"}]


def test_get_dms_channels_posts_with_selected_param():
    client, session = _client()
    session.post.return_value.json.return_value = []
    client.get_dms_channels()
    params = session.post.call_args.kwargs["params"]
    assert params == {"selected": "1"}


# ---------------------------------------------------------------------------
# get_available_channels
# ---------------------------------------------------------------------------


def test_get_available_channels_sends_ignore_ids():
    client, session = _client()
    session.post.return_value.json.return_value = []
    client.get_available_channels(["111", "222"])
    data = session.post.call_args.kwargs["data"]
    assert data == {"ignore": "111,222"}


# ---------------------------------------------------------------------------
# upload_transponders
# ---------------------------------------------------------------------------


def test_upload_transponders_sends_multipart():
    client, session = _client()
    client.upload_transponders({"GroupList": [], "SourceList": []})
    files = session.post.call_args.kwargs["files"]
    assert "transponderlist" in files
    filename, content, mime = files["transponderlist"]
    assert filename == "transponders.json"
    assert mime == "application/json"


# ---------------------------------------------------------------------------
# download_m3u
# ---------------------------------------------------------------------------


def test_download_m3u_returns_response_text():
    client, session = _client()
    session.get.return_value.text = "#EXTM3U\n"
    result = client.download_m3u()
    assert result == "#EXTM3U\n"


# ---------------------------------------------------------------------------
# poll_scan_until_complete
# ---------------------------------------------------------------------------


def test_poll_scan_returns_when_octopus_returns_404():
    client, session = _client()
    # Mock three 404s to satisfy the done_confirmations check
    session.get.return_value.status_code = 404
    session.get.return_value.text = ""
    result = client.poll_scan_until_complete(interval=0)
    assert result == {}


def test_poll_scan_raises_timeout():
    client, session = _client()
    session.get.return_value.json.return_value = {"running": True}
    with pytest.raises(TimeoutError):
        client.poll_scan_until_complete(interval=0, timeout=0)


# ---------------------------------------------------------------------------
# login classmethod
# ---------------------------------------------------------------------------


def test_login_raises_if_no_session_cookie():
    with patch("sat_to_ip.octopus.client.requests.Session") as mock_session_cls:
        session = MagicMock()
        session.cookies.__contains__ = lambda self, key: False
        mock_session_cls.return_value = session
        with pytest.raises(RuntimeError, match="Login failed"):
            OctopusClient.login("http://octopus", "admin", "wrong")


def test_login_returns_client_with_session():
    with patch("sat_to_ip.octopus.client.requests.Session") as mock_session_cls:
        session = MagicMock()
        session.cookies.__contains__ = lambda self, key: key == "session_id"
        mock_session_cls.return_value = session
        client = OctopusClient.login("http://octopus/", "admin", "secret")
    assert client.base_url == "http://octopus"
    assert client.session is session
    assert session.post.call_args.kwargs["data"]["passwd"] == "secret"


def _resp(status_code=200, text="", json_value=None, json_error=False):
    resp = MagicMock(status_code=status_code, text=text)
    if json_error:
        resp.json.side_effect = ValueError("truncated")
    else:
        resp.json.return_value = json_value
    return resp


def test_poll_scan_survives_transient_errors_and_bad_json_then_completes():
    client, session = _client()
    done = _resp(404)
    session.get.side_effect = [
        requests.exceptions.ConnectionError("reset"),
        requests.exceptions.Timeout("slow"),
        _resp(text="{", json_error=True),
        _resp(text="{}", json_value={"Progress": "42.5", "Channels found": 7, "Source List Name": "28.2E"}),
        _resp(text="{}", json_value={}),
        done,
        done,
        done,
    ]
    with patch("sat_to_ip.octopus.client.time.sleep"):
        assert client.poll_scan_until_complete(interval=0) == {}
    assert session.get.call_count == 8


def test_poll_scan_resets_done_count_when_scan_resumes():
    client, session = _client()
    done = _resp(404)
    session.get.side_effect = [done, done, _resp(text="{}", json_value={}), done, done, done]
    with patch("sat_to_ip.octopus.client.time.sleep"):
        client.poll_scan_until_complete(interval=0)
    assert session.get.call_count == 6


def test_save_channels_posts_list():
    client, session = _client()
    client.save_channels([{"name": "A"}])
    assert session.post.call_args[0][0] == "http://octopus/channels/save"
    assert session.post.call_args.kwargs["json"] == [{"name": "A"}]


def test_requests_have_timeout():
    client, session = _client()
    client.get_dms_channels()
    client.download_m3u()
    assert session.post.call_args.kwargs["timeout"] == 30
    assert session.get.call_args.kwargs["timeout"] == 30
