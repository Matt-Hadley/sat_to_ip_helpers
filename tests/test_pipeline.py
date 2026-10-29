import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

import pipeline
from king_of_sat_scraper.transponder import Transponder
from utils_pipeline import ConfigurationError, StateError, StepError

M3U = "#EXTM3U\n#EXTINF:-1,BBC One HD\nhttp://o/1\n#EXTINF:-1,Radio 4\nhttp://o/2\n"
DMS = [
    {"name": "BBC One HD", "type": "video", "serviceid": "1", "frequency": 10773},
    {"name": "Radio 4", "type": "audio", "serviceid": "2", "frequency": 11954},
]
GRACENOTE = [{"callSign": "BBC1HD", "name": "BBC One HD", "stationId": "111", "channel": "101"}]


def _args(tmp_path, *argv):
    return pipeline.build_parser().parse_args(["--state-dir", str(tmp_path), *argv])


def _transponder(satellite="Astra 2E"):
    return Transponder("28.2E", satellite, 10773.0, "H", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "", 2, 2045)


@pytest.fixture
def octopus():
    client = MagicMock()
    client.get_dms_channels.return_value = [dict(ch) for ch in DMS[:1]]
    client.get_available_channels.return_value = [dict(DMS[1])]
    client.download_m3u.return_value = M3U
    return client


# ---------------------------------------------------------------------------
# State and client helpers
# ---------------------------------------------------------------------------


def test_state_round_trip(tmp_path):
    pipeline.save_state(str(tmp_path / "s"), "data", {"a": 1})
    pipeline.save_state(str(tmp_path / "s"), "playlist", M3U, text=True)
    assert pipeline.load_state(str(tmp_path / "s"), "data") == {"a": 1}
    assert pipeline.load_state(str(tmp_path / "s"), "playlist", text=True) == M3U


def test_load_state_missing_file(tmp_path):
    with pytest.raises(StateError, match="Run earlier steps first"):
        pipeline.load_state(str(tmp_path), "nothing")


def test_octopus_requires_host(tmp_path):
    with pytest.raises(ConfigurationError, match="--octopus-host"):
        pipeline._octopus(_args(tmp_path))


def test_octopus_requires_password(tmp_path, monkeypatch):
    monkeypatch.delenv("OCTOPUS_PASSWORD", raising=False)
    with pytest.raises(ConfigurationError, match="OCTOPUS_PASSWORD"):
        pipeline._octopus(_args(tmp_path, "--octopus-host", "octo"))


def test_octopus_logs_in_with_env_password(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PASSWORD", "pw")
    with patch("pipeline.OctopusClient.login") as login:
        pipeline._octopus(_args(tmp_path, "--octopus-host", "octo"))
    login.assert_called_once_with(base_url="http://octo", username="admin", password="pw")


def test_channels_dvr_sid_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("CHANNELS_DVR_SID", raising=False)
    with patch("pipeline.ChannelsDVRClient.with_sid") as with_sid:
        pipeline._channels_dvr(_args(tmp_path))
    with_sid.assert_called_once_with(host="localhost", port=8089, sid=None)


def test_channels_dvr_uses_cli_sid(tmp_path):
    with patch("pipeline.ChannelsDVRClient.with_sid") as with_sid:
        pipeline._channels_dvr(_args(tmp_path, "--channels-dvr-sid", "abc"))
    with_sid.assert_called_once_with(host="localhost", port=8089, sid="abc")


def test_king_of_sat_factory(tmp_path):
    with patch("pipeline.KingOfSatClient.create") as create:
        pipeline._king_of_sat(_args(tmp_path))
    create.assert_called_once_with(base_url="https://en.kingofsat.net/freqs.php")


def test_cached_builds_client_once():
    factory = MagicMock(return_value="client")
    state: dict = {}
    assert pipeline._cached(state, "k", factory, "args") == "client"
    assert pipeline._cached(state, "k", factory, "args") == "client"
    factory.assert_called_once_with("args")


# ---------------------------------------------------------------------------
# Steps 1-3
# ---------------------------------------------------------------------------


def test_step_1_skips_failed_and_empty_positions(tmp_path):
    kos = MagicMock()
    kos.fetch_transponders.side_effect = [[_transponder()], RuntimeError("down"), []]
    state = {"king_of_sat": kos}
    pipeline.step_1(_args(tmp_path, "--positions", "28.2E", "13.0E", "19.2E"), state)
    sources = pipeline.load_state(str(tmp_path), "transponders")["SourceList"]
    assert [s["Title"] for s in sources] == ["28.2E - Astra 2E"]
    assert state["transponders"]["SourceList"] == sources


def test_step_1_fails_when_nothing_scraped(tmp_path):
    kos = MagicMock()
    kos.fetch_transponders.return_value = []
    with pytest.raises(StepError):
        pipeline.step_1(_args(tmp_path), {"king_of_sat": kos})


def test_step_2_uploads_saved_transponders(tmp_path, octopus):
    pipeline.save_state(str(tmp_path), "transponders", {"SourceList": []})
    pipeline.step_2(_args(tmp_path), {"octopus": octopus})
    octopus.upload_transponders.assert_called_once_with({"SourceList": []})


def test_step_3_scans_and_waits(tmp_path, octopus):
    pipeline.step_3(_args(tmp_path, "--scan-timeout", "5", "--scan-poll-interval", "2"), {"octopus": octopus})
    octopus.start_scan.assert_called_once_with(["28.2E"])
    octopus.poll_scan_until_complete.assert_called_once_with(interval=2, timeout=5)
    assert octopus.get_available_channels.call_count == 2


# ---------------------------------------------------------------------------
# Step 4
# ---------------------------------------------------------------------------


def test_step_4_editor_starts_from_current_dms(tmp_path, octopus, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with patch("pipeline.interactive_dms_editor", side_effect=lambda dms, avail: avail + dms) as editor:
        pipeline.step_4(_args(tmp_path), {"octopus": octopus})
    octopus.get_available_channels.assert_called_once_with(["1"])
    assert editor.call_args.args[0] == octopus.get_dms_channels.return_value
    saved = octopus.save_channels.call_args.args[0]
    assert [(c["name"], c["position"]) for c in saved] == [("Radio 4", 0), ("BBC One HD", 1)]
    assert pipeline.load_state(str(tmp_path), "dms_channels") == saved


def test_step_4_editor_cancel_leaves_dms(tmp_path, octopus, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with patch("pipeline.interactive_dms_editor", return_value=None):
        pipeline.step_4(_args(tmp_path), {"octopus": octopus})
    octopus.save_channels.assert_not_called()


def test_step_4_without_tty_or_spec_only_lists(tmp_path, octopus, monkeypatch, caplog):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    caplog.set_level(logging.INFO)
    pipeline.step_4(_args(tmp_path), {"octopus": octopus})
    octopus.save_channels.assert_not_called()
    assert "Radio 4  freq=11954" in caplog.text


def test_step_4_spec_keeps_channels_already_in_dms(tmp_path, octopus, caplog):
    pipeline.step_4(_args(tmp_path, "--add-channels", "BBC One HD,Radio 4,Missing"), {"octopus": octopus})
    saved = octopus.save_channels.call_args.args[0]
    assert [c["name"] for c in saved] == ["BBC One HD", "Radio 4"]
    assert "'Missing'" in caplog.text


def test_step_4_spec_matching_nothing_saves_nothing(tmp_path, octopus):
    pipeline.step_4(_args(tmp_path, "--add-channels", "Nope"), {"octopus": octopus})
    octopus.save_channels.assert_not_called()


def _big_dms(n):
    return [{"name": f"Ch {i}", "type": "video", "serviceid": str(i)} for i in range(n)]


def test_step_4_refuses_to_remove_more_than_limit(tmp_path, octopus, caplog):
    octopus.get_dms_channels.return_value = _big_dms(10)
    with pytest.raises(StepError, match="Refusing to remove 7 channels"):
        pipeline.step_4(_args(tmp_path, "--add-channels", "Ch 0,Ch 1,Ch 2", "--max-removed", "6"), {"octopus": octopus})
    octopus.save_channels.assert_not_called()
    assert "- Ch 9" in caplog.text


def test_step_4_removals_within_limit_are_saved(tmp_path, octopus):
    octopus.get_dms_channels.return_value = _big_dms(10)
    pipeline.step_4(_args(tmp_path, "--add-channels", "Ch 0,Ch 1,Ch 2", "--max-removed", "7"), {"octopus": octopus})
    assert len(octopus.save_channels.call_args.args[0]) == 3


def test_step_4_allow_removals_overrides_limit(tmp_path, octopus):
    octopus.get_dms_channels.return_value = _big_dms(10)
    pipeline.step_4(_args(tmp_path, "--add-channels", "Ch 0", "--allow-removals"), {"octopus": octopus})
    assert len(octopus.save_channels.call_args.args[0]) == 1


def test_resolve_add_channels_spec_reads_file(tmp_path):
    spec = tmp_path / "channels.txt"
    spec.write_text("# TV\nBBC One HD\n\nRadio 4@11954\n", encoding="utf-8")
    assert pipeline._resolve_add_channels_spec(str(spec)) == "BBC One HD,Radio 4@11954"
    assert pipeline._resolve_add_channels_spec("all") == "all"


# ---------------------------------------------------------------------------
# Steps 5-8
# ---------------------------------------------------------------------------


def test_step_5_splits_using_live_dms(tmp_path, octopus):
    octopus.get_dms_channels.return_value = DMS
    state: dict = {"octopus": octopus}
    pipeline.step_5(_args(tmp_path), state)
    assert "http://o/1" in state["octopus_m3u_tv"]
    assert "http://o/2" in pipeline.load_state(str(tmp_path), "octopus_m3u_radio", text=True)


def test_step_6_saves_gracenote(tmp_path):
    dvr = MagicMock()
    dvr.get_gracenote.return_value = GRACENOTE
    state: dict = {"channels_dvr": dvr}
    pipeline.step_6(_args(tmp_path), state)
    dvr.get_gracenote.assert_called_once_with("GBR-1000193-DEFAULT")
    assert pipeline.load_state(str(tmp_path), "gracenote") == GRACENOTE


def test_step_7_enriches_from_saved_state(tmp_path, caplog):
    pipeline.save_state(str(tmp_path), "octopus_m3u_tv", "#EXTM3U\n#EXTINF:-1,BBC One HD\nhttp://o/1\n", text=True)
    pipeline.save_state(str(tmp_path), "octopus_m3u_radio", "#EXTM3U\n#EXTINF:-1,Radio 4\nhttp://o/2\n", text=True)
    pipeline.save_state(str(tmp_path), "gracenote", GRACENOTE)
    state: dict = {}
    pipeline.step_7(_args(tmp_path, "--mapping-region", ""), state)
    assert 'channel-id="BBC1HD"' in state["enriched_m3u_tv"]
    assert "http://o/2" not in state["enriched_m3u_radio"]
    assert "No Gracenote match" in caplog.text


def test_step_7_loads_region_mappings(tmp_path):
    state = {"octopus_m3u_tv": "#EXTM3U\n", "octopus_m3u_radio": "#EXTM3U\n", "gracenote": GRACENOTE}
    with patch("pipeline.load_region_mappings", return_value={}) as load:
        pipeline.step_7(_args(tmp_path), state)
    load.assert_called_once_with("uk")


def test_step_7_refuses_when_too_few_channels_match_guide(tmp_path):
    state = {
        "octopus_m3u_tv": "#EXTM3U\n#EXTINF:-1,BBC One HD\nhttp://o/1\n#EXTINF:-1,Unknown\nhttp://o/9\n",
        "octopus_m3u_radio": "#EXTM3U\n",
        "gracenote": [{"callSign": "OTHER", "name": "Something Else"}],
    }
    with pytest.raises(StepError, match="Only 0/2 channels matched Gracenote"):
        pipeline.step_7(_args(tmp_path, "--mapping-region", ""), state)
    assert "enriched_m3u_tv" not in state
    with pytest.raises(StateError):
        pipeline.load_state(str(tmp_path), "enriched_m3u_tv", text=True)


def test_step_7_allows_empty_playlists(tmp_path):
    state = {"octopus_m3u_tv": "#EXTM3U\n", "octopus_m3u_radio": "#EXTM3U\n", "gracenote": GRACENOTE}
    pipeline.step_7(_args(tmp_path, "--mapping-region", ""), state)
    assert state["enriched_m3u_tv"] == "#EXTM3U\n"


def test_step_8_requires_a_source(tmp_path):
    with pytest.raises(ConfigurationError):
        pipeline.step_8(_args(tmp_path), {})


def test_step_8_pushes_tv_and_radio(tmp_path):
    dvr = MagicMock()
    state = {"channels_dvr": dvr, "enriched_m3u_tv": "tv", "enriched_m3u_radio": "radio"}
    args = _args(tmp_path, "--source-id", "TV", "--source-display-name", "Sat TV", "--source-id-radio", "R")
    pipeline.step_8(args, state)
    assert dvr.update_m3u_source.call_args_list[0].kwargs == {
        "source_id": "TV",
        "display_name": "Sat TV",
        "m3u_text": "tv",
    }
    assert dvr.update_m3u_source.call_args_list[1].kwargs == {
        "source_id": "R",
        "display_name": "R",
        "m3u_text": "radio",
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, argv, step):
    monkeypatch.setattr("sys.argv", ["pipeline.py", *argv])
    monkeypatch.setattr(pipeline, "STEPS", dict.fromkeys(pipeline.ALL_STEPS, step))
    pipeline.main()


def test_main_runs_requested_steps_in_order(monkeypatch):
    steps = {n: MagicMock(name=f"step_{n}") for n in pipeline.ALL_STEPS}
    parent = MagicMock()
    for n, step in steps.items():
        parent.attach_mock(step, f"step_{n}")
    monkeypatch.setattr("sys.argv", ["pipeline.py", "--steps", "3", "1"])
    monkeypatch.setattr(pipeline, "STEPS", steps)
    pipeline.main()
    assert [c[0] for c in parent.mock_calls] == ["step_1", "step_3"]


def test_main_runs_all_steps_by_default(monkeypatch):
    step = MagicMock()
    _run_main(monkeypatch, [], step)
    assert step.call_count == 8


@pytest.mark.parametrize(
    "error, message",
    [
        (StepError("boom"), "boom"),
        (TimeoutError("slow"), "--scan-timeout"),
        (requests.exceptions.ConnectionError("down"), "is the host reachable"),
        (requests.exceptions.HTTPError("500"), "HTTP error"),
        (KeyError("oops"), "unexpected error"),
    ],
)
def test_main_turns_errors_into_exit_messages(monkeypatch, error, message):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["--steps", "1"], MagicMock(side_effect=error))
    assert message in str(exc.value)
