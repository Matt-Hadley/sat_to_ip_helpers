import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sat_to_ip import pipeline
from sat_to_ip.checks import CheckSettings
from sat_to_ip.config import Config, Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.interfaces import SupportsRefresh, SupportsSelection
from sat_to_ip.kingofsat.transponder import Transponder
from sat_to_ip.model import ChannelRef, SelectionPlan
from sat_to_ip.octopus.receiver import OctopusReceiver, from_config
from tests.fakes import FakeGuide, FakePublisher

ENV = {"OCTO_PW": "secret"}


def _section(**data):
    return Section("receiver", {"type": "octopus", "host": "octo", "password_env": "OCTO_PW", **data}, Path("."), ENV)


def _ch(sid, name, kind="video", freq=10773):
    return {"serviceid": sid, "name": name, "type": kind, "frequency": freq, "extra": "kept"}


DMS = [_ch(1, "BBC One HD"), _ch(2, "Radio 4", "audio", 11954)]
AVAILABLE = [_ch(3, "ITV HD", freq=11386), _ch(4, "BBC One HD", freq=10847), _ch(5, "Classic FM", "audio")]


def _client(dms=DMS, available=AVAILABLE, m3u_text=""):
    client = MagicMock()
    client.get_dms_channels.side_effect = lambda: [dict(c) for c in dms]
    client.get_available_channels.side_effect = lambda ignore: [
        dict(c) for c in available if str(c["serviceid"]) not in ignore
    ]
    client.download_m3u.return_value = m3u_text
    return client


@pytest.fixture
def rx():
    receiver = OctopusReceiver(host="octo", password="pw")
    receiver._client = _client()
    return receiver


# from_config ---------------------------------------------------------------------------------


def test_from_config_defaults_and_no_io():
    with patch("sat_to_ip.octopus.receiver.OctopusClient") as cls:
        rx = from_config(_section())
    cls.login.assert_not_called()
    assert (rx.host, rx.password, rx.username, rx.positions) == ("octo", "secret", "admin", ["28.2E"])
    assert (rx.kingofsat_filter, rx.kingofsat_language, rx.scan_timeout, rx.scan_poll_interval) == (
        "Clear",
        "eng",
        600,
        1,
    )
    assert rx.name == "octopus" and "secret" not in repr(rx)
    assert isinstance(rx, SupportsRefresh) and isinstance(rx, SupportsSelection)


def test_from_config_reads_every_key():
    rx = from_config(
        _section(
            username="u",
            positions=["19.2E", "28.2E"],
            kingofsat_filter="All",
            kingofsat_language="ger",
            scan_timeout=30,
            scan_poll_interval=5,
        )
    )
    assert (rx.username, rx.positions, rx.kingofsat_filter, rx.kingofsat_language) == (
        "u",
        ["19.2E", "28.2E"],
        "All",
        "ger",
    )
    assert (rx.scan_timeout, rx.scan_poll_interval) == (30, 5)


@pytest.mark.parametrize(
    "data, message",
    [
        ({"host": None}, "receiver.host: required"),
        ({"password_env": None}, "receiver.password_env: required"),
        ({"password_env": "NOPE"}, "receiver.password_env: environment variable NOPE is not set"),
        ({"scan_timeout": 0}, "receiver.scan_timeout: must be at least 1"),
        ({"scan_poll_interval": 0}, "receiver.scan_poll_interval: must be at least 1"),
        ({"positions": [""]}, "receiver.positions: must be a list"),
    ],
)
def test_from_config_errors_name_the_key(data, message):
    section = _section(**data)
    section.data = {k: v for k, v in section.data.items() if v is not None}
    with pytest.raises(ConfigError, match=message):
        from_config(section)


def test_logs_in_once_lazily():
    rx = OctopusReceiver(host="octo", password="pw", username="me")
    with patch("sat_to_ip.octopus.receiver.OctopusClient.login", return_value=_client()) as login:
        rx.lineup()
        rx.lineup()
    login.assert_called_once_with("http://octo", "me", "pw")


# refresh -------------------------------------------------------------------------------------


def _transponder(sat="Astra 2E"):
    return Transponder("28.2°E", sat, 10773, "H", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "50", 2, 2045)


def _refresh(rx, fetch):
    kos = MagicMock()
    kos.fetch_transponders.side_effect = fetch
    with patch("sat_to_ip.octopus.receiver.KingOfSatClient.create", return_value=kos):
        rx.refresh()
    return kos


def test_refresh_uploads_scans_and_logs_diff(rx, caplog):
    caplog.set_level(logging.INFO)
    rx.positions = ["28.2E"]
    rx.scan_timeout, rx.scan_poll_interval = 99, 7
    before, after = [_ch(1, "Old")], [_ch(1, "Old"), _ch(9, "New")]
    rx._client.get_available_channels.side_effect = [before, after]
    kos = _refresh(rx, lambda pos, channel_filter, cl: [_transponder()])

    kos.fetch_transponders.assert_called_once_with("28.2E", channel_filter="Clear", cl="eng")
    payload = rx._client.upload_transponders.call_args.args[0]
    assert [s["Title"] for s in payload["SourceList"]] == ["28.2E - Astra 2E"]
    rx._client.start_scan.assert_called_once_with(["28.2E"])
    rx._client.poll_scan_until_complete.assert_called_once_with(interval=7, timeout=99)
    assert "+ New" in caplog.text


def test_refresh_skips_failing_and_empty_positions(rx, caplog):
    rx.positions = ["1E", "2E", "28.2E"]

    def fetch(pos, **_):
        if pos == "1E":
            raise RuntimeError("down")
        return [] if pos == "2E" else [_transponder()]

    _refresh(rx, fetch)
    payload = rx._client.upload_transponders.call_args.args[0]
    assert len(payload["SourceList"]) == 1
    assert "1E: down" in caplog.text and "no transponders found for 2E" in caplog.text


def test_refresh_fails_when_no_position_worked(rx):
    rx.positions = ["1E", "2E"]
    with pytest.raises(IntegrationError, match="no transponders for any position"):
        _refresh(rx, lambda pos, **_: [])
    rx._client.upload_transponders.assert_not_called()
    rx._client.start_scan.assert_not_called()


# plan_selection ------------------------------------------------------------------------------


def _keys(refs):
    return [r.key for r in refs]


def test_plan_current_is_dms_and_available_ignores_dms(rx):
    plan = rx.plan_selection([])
    assert plan.current == (ChannelRef("octopus:1", "BBC One HD"), ChannelRef("octopus:2", "Radio 4"))
    assert plan.proposed == ()
    rx._client.get_available_channels.assert_called_once_with(["1", "2"])


@pytest.mark.parametrize(
    "specs, keys",
    [
        (["all"], ["octopus:1", "octopus:2", "octopus:3", "octopus:4", "octopus:5"]),
        (["tv"], ["octopus:1", "octopus:3", "octopus:4"]),
        (["video"], ["octopus:1", "octopus:3", "octopus:4"]),
        (["radio"], ["octopus:2", "octopus:5"]),
        (["audio"], ["octopus:2", "octopus:5"]),
        (["itv hd", "Radio 4"], ["octopus:3", "octopus:2"]),  # spec order, case-insensitive
        (["BBC One HD", "BBC One HD"], ["octopus:1", "octopus:4"]),  # plain name takes the next untaken match
        (["BBC One HD@10847"], ["octopus:4"]),
        (["Radio 4", "radio"], ["octopus:2", "octopus:5"]),  # never the same channel twice
    ],
)
def test_plan_spec_forms(rx, specs, keys):
    plan = rx.plan_selection(specs)
    assert _keys(plan.proposed) == keys and plan.unmatched_specs == ()


def test_plan_unmatched_specs(rx):
    plan = rx.plan_selection(["Nope", "BBC One HD@1", "Radio 4", "Radio 4"])
    assert _keys(plan.proposed) == ["octopus:2"]
    assert plan.unmatched_specs == ("Nope", "BBC One HD@1", "Radio 4")
    assert _keys(plan.removed) == ["octopus:1"] and plan.added == ()


# apply_selection -----------------------------------------------------------------------------


def test_apply_saves_plan_in_order_with_positions(rx):
    plan = rx.plan_selection(["Classic FM", "BBC One HD"])
    rx._client.get_dms_channels.reset_mock()
    rx.apply_selection(plan)
    saved = rx._client.save_channels.call_args.args[0]
    assert [(c["serviceid"], c["position"], c["extra"]) for c in saved] == [(5, 0, "kept"), (1, 1, "kept")]
    rx._client.get_dms_channels.assert_not_called()  # planned dicts reused


def test_apply_refetches_for_a_plan_made_elsewhere(rx):
    rx.apply_selection(SelectionPlan((), (ChannelRef("octopus:3", "ITV HD"),)))
    rx._client.get_dms_channels.assert_called_once()
    assert [c["serviceid"] for c in rx._client.save_channels.call_args.args[0]] == [3]


def test_apply_unknown_key_saves_nothing(rx):
    plan = SelectionPlan((), (ChannelRef("octopus:1", "BBC One HD"), ChannelRef("octopus:404", "Gone")))
    with pytest.raises(IntegrationError, match="octopus:404"):
        rx.apply_selection(plan)
    rx._client.save_channels.assert_not_called()


# interactive ---------------------------------------------------------------------------------


def test_interactive_converts_editor_result(rx):
    with patch("sat_to_ip.octopus.receiver.dms_editor.interactive_dms_editor") as editor:
        editor.side_effect = lambda dms, available: [available[0], dms[1]]
        plan = rx.plan_selection_interactive()
    assert [c["serviceid"] for c in editor.call_args.args[0]] == [1, 2]
    assert [c["serviceid"] for c in editor.call_args.args[1]] == [3, 4, 5]
    assert _keys(plan.current) == ["octopus:1", "octopus:2"]
    assert _keys(plan.proposed) == ["octopus:3", "octopus:2"]
    rx.apply_selection(plan)
    assert [c["position"] for c in rx._client.save_channels.call_args.args[0]] == [0, 1]


def test_interactive_cancel_returns_none(rx):
    with patch("sat_to_ip.octopus.receiver.dms_editor.interactive_dms_editor", return_value=None):
        assert rx.plan_selection_interactive() is None


# lineup --------------------------------------------------------------------------------------

M3U = """#EXTM3U
#EXTINF:-1,BBC One HD
http://octo/1
#EXTINF:-1,Radio 4
http://octo/2
#EXTINF:-1,BBC One HD
http://octo/4
"""


def test_lineup_maps_dms_order_kinds_and_duplicate_names(caplog):
    dms = [
        _ch(4, "BBC One HD", freq=10847),
        {"name": "Radio 4", "type": "audio", "freq": "11954"},
        _ch(7, "Missing"),
        _ch(1, "BBC One HD", freq="?"),
    ]
    rx = OctopusReceiver(host="octo", password="pw")
    rx._client = _client(dms=dms, m3u_text=M3U)
    lineup = rx.lineup()

    assert [(c.key, c.name, c.kind, c.stream_url, c.number) for c in lineup] == [
        ("octopus:4", "BBC One HD", "tv", "http://octo/1", 1),
        ("octopus:Radio 4@11954", "Radio 4", "radio", "http://octo/2", 2),
        ("octopus:1", "BBC One HD", "tv", "http://octo/4", 3),
    ]
    assert [(c.frequency_mhz, c.service_id) for c in lineup] == [(10847.0, "4"), (11954.0, None), (None, "1")]
    assert "'Missing' is saved on the Octopus but missing from its M3U" in caplog.text


# pipeline ------------------------------------------------------------------------------------


def test_pipeline_runs_against_octopus_receiver(rx, tmp_path):
    rx._client.download_m3u.return_value = M3U
    specs = tmp_path / "channels.txt"
    specs.write_text("BBC One HD\nRadio 4\nITV HD\n")
    pub = FakePublisher()
    config = Config(rx, {"guide": FakeGuide()}, (pub,), CheckSettings(), specs, tmp_path / "c.toml")

    with patch("sat_to_ip.octopus.receiver.KingOfSatClient.create") as create:
        create.return_value.fetch_transponders.return_value = [_transponder()]
        report = pipeline.run(config)

    assert report.ok, report.summary()
    assert [o.name for o in report.outcomes] == ["refresh", "select", "publish:pub"]
    assert [c["serviceid"] for c in rx._client.save_channels.call_args.args[0]] == [1, 2, 3]
    lineup, _, _ = pub.published[0]
    assert [c.name for c in lineup] == ["BBC One HD", "Radio 4"]
    json.dumps(rx._client.save_channels.call_args.args[0])  # what goes to the Octopus is plain JSON
