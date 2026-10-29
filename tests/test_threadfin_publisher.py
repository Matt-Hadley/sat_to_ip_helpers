import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip.config import Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.model import GuideMatch, GuideRef, Lineup
from sat_to_ip.threadfin.publisher import ThreadfinPublisher, from_config
from tests.contracts import assert_publisher_contract
from tests.fakes import channel


def _section(tmp_path, **data):
    data = {k: v for k, v in {"output_dir": "out", **data}.items() if v is not None}  # None drops a key
    return Section("publishers[1]", data, tmp_path, {"TF_PASS": "pw"}, "threadfin")


def _lineup():
    return Lineup((channel(1, "BBC One"), channel(2, "Unmatched"), channel(3, "Radio 4", kind="radio")))


def _guide(xmltv=b"<tv/>"):
    refs = {"fake:1": GuideRef("bbc1.uk"), "fake:3": GuideRef("radio4.uk")}
    return GuideMatch("freeview", "xmltv", refs, (channel(2, "Unmatched"),), xmltv)


def _reply(text, status=200):
    resp = MagicMock(status_code=status, text=text)
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    return resp


def _ok(token=None):
    return _reply(json.dumps({"status": True, **({"token": token} if token else {})}))


def _publisher(tmp_path, *replies, **kw):
    session = MagicMock()
    session.post.side_effect = list(replies)
    return ThreadfinPublisher("threadfin", tmp_path / "out", session=session, guide="freeview", **kw), session


def _bodies(session):
    return [c.kwargs["json"] for c in session.post.call_args_list]


# config -----------------------------------------------------------------------------------------


def test_from_config_files_only(tmp_path):
    pub = from_config(_section(tmp_path, guide="freeview"))
    assert pub.name == "threadfin" and pub.output_dir == tmp_path / "out" and pub.guide == "freeview"
    assert pub.api_url is None and pub.accepts_guides == {"xmltv"}


def test_from_config_with_api_and_login(tmp_path):
    pub = from_config(_section(tmp_path, api_url="http://tf:34400/", username="sat", password_env="TF_PASS"))
    assert (pub.api_url, pub.username, pub.password) == ("http://tf:34400", "sat", "pw")
    assert "pw" not in repr(pub)


def test_from_config_does_no_io(tmp_path):
    with patch.object(requests.Session, "request", side_effect=AssertionError("network")):
        from_config(_section(tmp_path, api_url="http://tf:34400"))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "data, key",
    [
        ({"output_dir": None}, "output_dir"),
        ({"api_url": "tf:34400"}, "api_url"),
        ({"api_url": "http://tf", "username": "sat"}, "password_env"),
        ({"api_url": "http://tf", "username": "sat", "password_env": "NOPE"}, "password_env"),
        ({"api_url": "http://tf", "password_env": "TF_PASS"}, "username"),
        ({"username": "sat", "password_env": "TF_PASS"}, "api_url"),
    ],
)
def test_from_config_errors_name_their_key(tmp_path, data, key):
    with pytest.raises(ConfigError, match=rf"publishers\[1\]\.{key}:"):
        from_config(_section(tmp_path, **data))


# publish ----------------------------------------------------------------------------------------


def test_files_only_makes_no_calls(tmp_path):
    pub, session = _publisher(tmp_path)
    result = pub.publish(_lineup(), _guide(), dry_run=False)
    assert result.channels == 2 and "on its schedule" in result.detail
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"tv.m3u", "radio.m3u", "guide.xml"}
    session.post.assert_not_called()


def test_api_updates_xmltv_then_m3u(tmp_path):
    pub, session = _publisher(tmp_path, _ok(), _ok(), api_url="http://tf:34400")
    result = pub.publish(_lineup(), _guide(), dry_run=False)
    assert _bodies(session) == [{"cmd": "update.xmltv"}, {"cmd": "update.m3u"}]
    assert session.post.call_args.args == ("http://tf:34400/api/",)
    assert "update.xmltv, update.m3u" in result.detail


def test_api_without_guide_file_updates_m3u_only(tmp_path):
    pub, session = _publisher(tmp_path, _ok(), api_url="http://tf:34400")
    pub.publish(_lineup(), _guide(xmltv=None), dry_run=False)
    assert _bodies(session) == [{"cmd": "update.m3u"}]


def test_api_login_passes_each_new_token_on(tmp_path):
    pub, session = _publisher(
        tmp_path, _ok("t1"), _ok("t2"), _ok("t3"), api_url="http://tf", username="sat", password="pw"
    )
    pub.publish(_lineup(), _guide(), dry_run=False)
    assert _bodies(session) == [
        {"cmd": "login", "username": "sat", "password": "pw"},
        {"cmd": "update.xmltv", "token": "t1"},
        {"cmd": "update.m3u", "token": "t2"},
    ]


def test_dry_run_writes_only_to_temp_and_makes_no_calls(tmp_path):
    pub, session = _publisher(tmp_path, api_url="http://tf")
    result = pub.publish(_lineup(), _guide(), dry_run=True)
    assert not (tmp_path / "out").exists() and "dry run" in result.detail
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "reply, message",
    [
        (_reply("", 423), "switched off"),
        (_reply("", 500), "500 error"),
        # Threadfin writes the error and then its normal reply on a failed command
        (_reply('{"err":"Login incorrect","status":false}{"status":true}'), "update.xmltv: Login incorrect"),
        (_reply('{"status":false}'), "refused"),
        (_reply("[1]"), r"\[1\]"),
        (_reply("<html>"), "files written"),
    ],
)
def test_api_failure_fails_after_files_are_written(tmp_path, reply, message):
    pub, _ = _publisher(tmp_path, reply, api_url="http://tf")
    with pytest.raises(IntegrationError, match=message) as err:
        pub.publish(_lineup(), _guide(), dry_run=False)
    assert "files written to" in str(err.value)
    assert (tmp_path / "out" / "tv.m3u").exists()


def test_contract(tmp_path):
    pub, _ = _publisher(tmp_path, api_url="http://tf")
    assert_publisher_contract(pub, _lineup(), _guide())
    assert_publisher_contract(ThreadfinPublisher("tf", tmp_path / "o", session=MagicMock()), _lineup(), None)
