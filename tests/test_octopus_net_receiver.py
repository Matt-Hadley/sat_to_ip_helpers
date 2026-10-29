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
from sat_to_ip.model import ChannelRef
from sat_to_ip.octopus_net.channels import channel_key
from sat_to_ip.octopus_net.receiver import OctopusNetReceiver, from_config
from sat_to_ip.selection import specs_from_config
from tests.contracts import assert_receiver_contract
from tests.fakes import FakeGuide, FakePublisher

ENV = {"OCTO_PW": "secret"}


def _section(**data):
    return Section(
        "receiver", {"type": "octopus_net", "host": "octo", "password_env": "OCTO_PW", **data}, Path("."), ENV
    )


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
    receiver = OctopusNetReceiver(host="octo", password="pw")
    receiver._client = _client()
    return receiver


# from_config ---------------------------------------------------------------------------------


def test_from_config_defaults_and_no_io():
    with patch("sat_to_ip.octopus_net.receiver.OctopusClient") as cls:
        rx = from_config(_section())
    cls.login.assert_not_called()
    assert (rx.host, rx.password, rx.username, rx.positions) == ("octo", "secret", "admin", ["28.2E"])
    assert (rx.kingofsat_filter, rx.kingofsat_language, rx.scan_timeout, rx.scan_poll_interval) == (
        "Clear",
        "eng",
        600,
        1,
    )
    assert rx.name == "octopus_net" and "secret" not in repr(rx)
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
    rx = OctopusNetReceiver(host="octo", password="pw", username="me")
    with patch("sat_to_ip.octopus_net.receiver.OctopusClient.login", return_value=_client()) as login:
        rx.lineup()
        rx.lineup()
    login.assert_called_once_with("http://octo", "me", "pw")


# refresh -------------------------------------------------------------------------------------


def _transponder(sat="Astra 2E", freq=10773):
    return Transponder(sat, freq, "H", "DVB-S2", "8PSK", 23000)


def _refresh(rx, fetch):
    kos = MagicMock()
    kos.fetch_transponders.side_effect = fetch
    with patch("sat_to_ip.octopus_net.receiver.KingOfSatClient.create", return_value=kos):
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
    rx._client.get_tuner_status.assert_not_called()


def test_refresh_logs_tuner_status_when_the_scan_finds_nothing(rx, caplog):
    rx._client.get_available_channels.side_effect = [[], []]
    rx._client.get_tuner_status.return_value = [
        {"Input": "1", "Lock": False, "Strength": -39020, "SNR": 0},
        {"Input": "2", "Lock": True, "Strength": -38390, "SNR": 16310},
    ]
    _refresh(rx, lambda pos, channel_filter, cl: [_transponder()])
    assert "the scan found no channels" in caplog.text
    assert "Input 1: no lock, strength -39.0 dBm, SNR 0.0 dB" in caplog.text
    assert "Input 2: locked, strength -38.4 dBm, SNR 16.3 dB" in caplog.text


def test_refresh_carries_on_when_tuner_status_fails_after_an_empty_scan(rx, caplog):
    rx._client.get_available_channels.side_effect = [[], []]
    rx._client.get_tuner_status.side_effect = IntegrationError("HTTP 500")
    _refresh(rx, lambda pos, channel_filter, cl: [_transponder()])
    assert "tuner status couldn't be read: HTTP 500" in caplog.text


def _moved(sid, name, freq, dvb, kind="video"):
    return {**_ch(sid, name, kind, freq), "dvb_serviceid": dvb}


SAVED = [_moved(1, "BBC One HD", 10773, 6301), _moved(2, "Radio 4", 11954, 6401, "audio")]


@pytest.mark.parametrize(
    "live",
    [
        [10847, 11954, 11000],  # KingOfSat no longer lists 10773
        [10773, 10847, 11954, 11000],  # it still lists 10773, but the scan found nothing there (GB News, 2026-10-29)
    ],
)
def test_refresh_retunes_a_saved_channel_the_scan_found_elsewhere(rx, caplog, live):
    caplog.set_level(logging.INFO)
    rx._client = _client(dms=SAVED)
    # BBC One HD moved: a new serviceid on 10847, none on 10773; Radio 4 didn't move
    scanned = [SAVED[1], _moved(9, "BBC One HD", 10847, 6301), _moved(8, "BBC One HD", 11000, 9999)]
    rx._client.get_available_channels.side_effect = [SAVED, scanned]
    _refresh(rx, lambda pos, **_: [_transponder(freq=f) for f in live])
    records = rx._client.save_channels.call_args.args[0]
    assert [(r["serviceid"], r["frequency"], r["position"]) for r in records] == [(9, 10847, 0), (2, 11954, 1)]
    assert "Retuning 'BBC One HD': 10773 → 10847 MHz" in caplog.text


@pytest.mark.parametrize(
    "scanned",
    [
        [*SAVED],  # both still found where they were saved
        [*SAVED, _moved(9, "BBC One HD", 10847, 6301)],  # a copy elsewhere, but the saved one still airs
        [SAVED[1], _moved(9, "BBC One HD", 10847, 1234)],  # same name, different DVB service ID
        [SAVED[1], _moved(9, "BBC One HD", 10847, 6301), _moved(8, "BBC One HD", 10850, 6301)],  # two candidates
        [SAVED[1], _moved(9, "BBC One HD", "", 6301)],  # a candidate without a frequency
    ],
)
def test_refresh_saves_nothing_without_one_clear_replacement(rx, scanned):
    rx._client = _client(dms=SAVED)
    rx._client.get_available_channels.side_effect = [SAVED, scanned]
    _refresh(rx, lambda pos, **_: [_transponder(freq=f) for f in (10773, 10847, 10850, 11954)])
    rx._client.save_channels.assert_not_called()


def test_refresh_retunes_nothing_when_a_position_failed(rx):
    rx.positions = ["1E", "28.2E"]
    rx._client = _client(dms=SAVED)
    rx._client.get_available_channels.side_effect = [SAVED, [*SAVED, _moved(9, "BBC One HD", 10847, 6301)]]
    _refresh(rx, lambda pos, **_: [] if pos == "1E" else [_transponder(freq=10847), _transponder(freq=11954)])
    rx._client.save_channels.assert_not_called()


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


# selection -----------------------------------------------------------------------------------


def _keys(refs):
    return [r.key for r in refs]


def test_candidates_are_saved_then_found_with_kind_and_frequency(rx):
    saved, found = rx.selection_candidates()
    assert saved == [
        ChannelRef("octopus_net:1@10773", "BBC One HD", "tv", 10773.0),
        ChannelRef("octopus_net:2@11954", "Radio 4", "radio", 11954.0),
    ]
    assert _keys(found) == ["octopus_net:3@11386", "octopus_net:4@10847", "octopus_net:5@10773"]


def test_channels_sharing_a_service_id_on_different_frequencies_stay_apart():
    dms = [_ch(1, "BBC One HD"), _ch(1, "Other TV", freq=12000)]
    scanned = [_ch(1, "BBC One HD", freq="10773.0"), _ch(1, "Third TV", freq=11500)]  # the first is already saved
    rx = OctopusNetReceiver(host="octo", password="pw")
    rx._client = _client(dms=dms, available=scanned)
    saved, found = rx.selection_candidates()
    assert _keys(saved) == ["octopus_net:1@10773", "octopus_net:1@12000"]
    assert _keys(found) == ["octopus_net:1@11500"]


@pytest.mark.parametrize("freq", ["nan", "inf", float("-inf")])
def test_a_frequency_that_isnt_a_number_counts_as_none(freq):
    assert channel_key(_ch(1, "BBC One HD", freq=freq)) == "octopus_net:1"


def test_a_saved_channel_without_a_frequency_hides_scanned_ones_with_its_service_id():
    rx = OctopusNetReceiver(host="octo", password="pw")
    rx._client = _client(dms=[_ch(1, "BBC One HD", freq=None)], available=[_ch(1, "BBC One HD"), _ch(3, "ITV HD")])
    saved, found = rx.selection_candidates()
    assert _keys(saved) == ["octopus_net:1"] and _keys(found) == ["octopus_net:3@10773"]


def test_save_selection_saves_records_in_order_with_positions(rx):
    saved, found = rx.selection_candidates()
    rx.save_selection([found[2], saved[0]])
    records = rx._client.save_channels.call_args.args[0]
    assert [(r["serviceid"], r["position"], r["extra"]) for r in records] == [(5, 0, "kept"), (1, 1, "kept")]


def test_save_selection_refetches_for_refs_made_elsewhere(rx):
    rx.save_selection([ChannelRef("octopus_net:3@11386", "ITV HD")])
    assert [r["serviceid"] for r in rx._client.save_channels.call_args.args[0]] == [3]


def test_save_selection_unknown_key_saves_nothing(rx):
    refs = [ChannelRef("octopus_net:1@10773", "BBC One HD"), ChannelRef("octopus_net:404", "Gone")]
    with pytest.raises(IntegrationError, match="octopus_net:404"):
        rx.save_selection(refs)
    rx._client.save_channels.assert_not_called()


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
    rx = OctopusNetReceiver(host="octo", password="pw")
    rx._client = _client(dms=dms, m3u_text=M3U)
    lineup = rx.lineup()

    assert [(c.key, c.name, c.kind, c.stream_url, c.number) for c in lineup] == [
        ("octopus_net:4@10847", "BBC One HD", "tv", "http://octo/1", 0),
        ("octopus_net:Radio 4@11954", "Radio 4", "radio", "http://octo/2", 0),
        ("octopus_net:1", "BBC One HD", "tv", "http://octo/4", 0),
    ]
    assert [c.frequency_mhz for c in lineup] == [10847.0, 11954.0, None]
    assert "'Missing' is saved on the Octopus NET but missing from its M3U" in caplog.text


# pipeline ------------------------------------------------------------------------------------


def test_pipeline_runs_against_octopus_net_receiver(rx, tmp_path):
    rx._client.download_m3u.return_value = M3U
    specs = specs_from_config([{"names": ["BBC One HD", "Radio 4", "ITV HD"]}])
    pub = FakePublisher()
    config = Config(rx, {"guide": FakeGuide()}, (pub,), CheckSettings(), tmp_path / "c.toml", tuple(specs))

    with patch("sat_to_ip.octopus_net.receiver.KingOfSatClient.create") as create:
        create.return_value.fetch_transponders.return_value = [_transponder()]
        report = pipeline.run(config)

    assert report.ok, report.summary()
    assert [o.name for o in report.outcomes] == ["refresh", "select", "publish:pub"]
    assert [c["serviceid"] for c in rx._client.save_channels.call_args.args[0]] == [1, 2, 3]
    lineup, _, _ = pub.published[0]
    assert [(c.number, c.name) for c in lineup] == [(1, "BBC One HD"), (2, "Radio 4")]
    json.dumps(rx._client.save_channels.call_args.args[0])  # what goes to the Octopus is plain JSON


def test_host_may_include_a_scheme():
    rx = OctopusNetReceiver(host="https://octo", password="pw")
    with patch("sat_to_ip.octopus_net.receiver.OctopusClient.login", return_value=_client()) as login:
        rx.lineup()
    login.assert_called_once_with("https://octo", "admin", "pw")


def test_channel_without_a_type_is_tv():
    rx = OctopusNetReceiver(host="octo", password="pw")
    rx._client = _client(dms=[{"serviceid": 9, "name": "BBC One HD"}], m3u_text=M3U)
    assert [c.kind for c in rx.lineup()] == ["tv"]


def test_octopus_net_receiver_meets_the_receiver_contract(rx):
    rx._client = _client(dms=[_ch(1, "BBC One HD"), _ch(2, "Radio 4", "audio", 11954)], m3u_text=M3U)
    assert_receiver_contract(rx)
