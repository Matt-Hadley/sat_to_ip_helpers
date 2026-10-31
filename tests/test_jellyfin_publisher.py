from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip import m3u, pipeline
from sat_to_ip.checks import CheckSettings
from sat_to_ip.config import Config, Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.jellyfin.publisher import JellyfinPublisher, from_config
from sat_to_ip.model import GuideMatch, GuideRef, Lineup
from sat_to_ip.pipeline import RunOptions, Stage
from tests.contracts import assert_publisher_contract
from tests.fakes import FakeReceiver, channel

TASKS = [{"Key": "RefreshLibrary", "Id": "lib"}, {"Key": "RefreshGuide", "Id": "abc123"}]


def _section(tmp_path, env=None, **data):
    data = {k: v for k, v in {"output_dir": "out", **data}.items() if v is not None}  # None drops a key
    return Section("publishers[0]", data, tmp_path, env or {"JF_KEY": "secret"}, "jellyfin")


def _lineup():
    return Lineup((channel(1, "BBC One"), channel(2, "Unmatched"), channel(3, "Radio 4", kind="radio")))


def _guide():
    refs = {"fake:1": GuideRef("bbc1.uk"), "fake:3": GuideRef("radio4.uk")}
    return GuideMatch("freeview", "xmltv", refs, (channel(2, "Unmatched"),), b"<tv/>")


def _publisher(tmp_path, **kw):
    session = MagicMock()
    session.get.return_value.json.return_value = TASKS
    kw.setdefault("api_key", "secret")
    return JellyfinPublisher("jellyfin", tmp_path / "out", session=session, guide="freeview", **kw), session


# config -----------------------------------------------------------------------------------------


def test_from_config_defaults(tmp_path):
    pub = from_config(_section(tmp_path, api_key_env="JF_KEY", guide="freeview"))
    assert pub.name == "jellyfin" and pub.url == "http://localhost:8096" and pub.api_key == "secret"
    assert "secret" not in repr(pub)
    assert pub.output_dir == tmp_path / "out" and pub.refresh is True and pub.guide == "freeview"
    assert pub.accepts_guides == {"xmltv"}


def test_from_config_does_no_io(tmp_path):
    with patch.object(requests.Session, "request", side_effect=AssertionError("network")):
        from_config(_section(tmp_path, api_key_env="JF_KEY"))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "data, key",
    [
        ({}, "api_key_env"),
        ({"api_key_env": "MISSING"}, "api_key_env"),
        ({"api_key_env": "JF_KEY", "output_dir": None}, "output_dir"),
        ({"api_key_env": "JF_KEY", "url": "localhost:8096"}, "url"),
        ({"api_key_env": "JF_KEY", "refresh": "yes"}, "refresh"),
    ],
)
def test_from_config_errors_name_their_key(tmp_path, data, key):
    with pytest.raises(ConfigError, match=rf"publishers\[0\]\.{key}:"):
        from_config(_section(tmp_path, **data))


def test_api_key_not_needed_without_refresh(tmp_path):
    pub = from_config(_section(tmp_path, refresh=False, url="http://jf:8096/"))
    assert pub.api_key is None and pub.url == "http://jf:8096"


# publish ----------------------------------------------------------------------------------------


def test_publish_writes_files_then_starts_refresh_guide(tmp_path):
    pub, session = _publisher(tmp_path)
    result = pub.publish(_lineup(), _guide(), dry_run=False)

    assert result.channels == 2 and "started Refresh Guide" in result.detail and str(tmp_path / "out") in result.detail
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"tv.m3u", "radio.m3u", "guide.xml"}
    headers = {"Authorization": 'MediaBrowser Token="secret"'}
    session.get.assert_called_once_with("http://localhost:8096/ScheduledTasks", headers=headers)
    session.post.assert_called_once_with("http://localhost:8096/ScheduledTasks/Running/abc123", headers=headers)


def test_refresh_off_makes_no_calls(tmp_path):
    pub, session = _publisher(tmp_path, refresh=False)
    result = pub.publish(_lineup(), _guide(), dry_run=False)
    assert "guide refresh off" in result.detail and (tmp_path / "out" / "tv.m3u").exists()
    session.get.assert_not_called()
    session.post.assert_not_called()


def test_dry_run_writes_nothing_but_checks_the_api_key_and_task(tmp_path):
    pub, session = _publisher(tmp_path)
    result = pub.publish(_lineup(), _guide(), dry_run=True)
    assert not (tmp_path / "out").exists()
    assert result.detail.startswith("dry run: would write") and "Refresh Guide task found (abc123)" in result.detail
    session.get.assert_called_once()
    session.post.assert_not_called()


def test_dry_run_fails_on_a_bad_api_key(tmp_path):
    pub, session = _publisher(tmp_path)
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    with pytest.raises(IntegrationError, match="can't read Jellyfin's scheduled tasks .*401"):
        pub.publish(_lineup(), _guide(), dry_run=True)
    assert not (tmp_path / "out").exists()


def test_non_json_task_list(tmp_path):
    pub, session = _publisher(tmp_path)
    session.get.return_value.json.side_effect = ValueError("html")
    with pytest.raises(IntegrationError, match="unexpected reply"):
        pub.publish(_lineup(), _guide(), dry_run=False)


@pytest.mark.parametrize("reply", [{"error": "proxy"}, ["RefreshGuide"]])
def test_a_reply_that_is_not_a_task_list(tmp_path, reply):
    pub, session = _publisher(tmp_path)
    session.get.return_value.json.return_value = reply
    with pytest.raises(IntegrationError, match="not a task list"):
        pub.publish(_lineup(), _guide(), dry_run=False)


def test_task_not_found_fails_after_files_are_written(tmp_path):
    pub, session = _publisher(tmp_path)
    session.get.return_value.json.return_value = [{"Key": "Other", "Id": "x"}]
    with pytest.raises(IntegrationError, match="files written to .*RefreshGuide"):
        pub.publish(_lineup(), _guide(), dry_run=False)
    assert (tmp_path / "out" / "tv.m3u").exists()
    session.post.assert_not_called()


@pytest.mark.parametrize("call", ["get", "post"])
def test_http_error_fails_after_files_are_written(tmp_path, call):
    pub, session = _publisher(tmp_path)
    getattr(session, call).return_value.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    with pytest.raises(IntegrationError, match="files written to .*401 Unauthorized"):
        pub.publish(_lineup(), _guide(), dry_run=False)
    assert (tmp_path / "out" / "guide.xml").exists()


def test_contract(tmp_path):
    pub, _ = _publisher(tmp_path)
    assert_publisher_contract(pub, _lineup(), _guide())
    no_refresh = JellyfinPublisher("jf", tmp_path / "o", refresh=False, session=MagicMock())
    assert_publisher_contract(no_refresh, _lineup(), None)


# pipeline ---------------------------------------------------------------------------------------


@dataclass
class XmltvGuide:
    name: str = "freeview"
    kind: str = "xmltv"

    def match(self, lineup: Lineup) -> GuideMatch:
        refs = {c.key: GuideRef(f"{c.name.lower().replace(' ', '')}.uk") for c in lineup}
        return GuideMatch(self.name, self.kind, refs, (), b"<tv/>")


def test_pipeline_run_with_real_publisher(tmp_path):
    pub, session = _publisher(tmp_path)
    config = Config(
        receiver=FakeReceiver(),
        guides={"freeview": XmltvGuide()},
        publishers=(pub,),
        checks=CheckSettings(),
        path=Path(tmp_path / "c.toml"),
    )
    report = pipeline.run(config, RunOptions(stages=frozenset({Stage.PUBLISH})))

    assert report.exit_code == 0, report.summary()
    assert [dict(e.attrs).get("tvg-id") for e in m3u.parse((tmp_path / "out" / "tv.m3u").read_text())] == [
        "ch1.uk",
        "ch2.uk",
    ]
    assert [e.label for e in m3u.parse((tmp_path / "out" / "radio.m3u").read_text())] == ["3 Ch 3"]
    assert (tmp_path / "out" / "guide.xml").read_bytes() == b"<tv/>"
    session.post.assert_called_once()
