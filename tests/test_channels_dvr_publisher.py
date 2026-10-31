from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip import pipeline, registry
from sat_to_ip.channels_dvr.publisher import ChannelsDVRPublisher, Source
from sat_to_ip.checks import CheckSettings
from sat_to_ip.config import Config, Section
from sat_to_ip.exceptions import CheckError, ConfigError, IntegrationError
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Lineup
from sat_to_ip.pipeline import RunOptions, Stage
from tests.contracts import assert_publisher_contract
from tests.fakes import FakeReceiver
from tests.test_channels_dvr_guide import GOLDEN
from tests.test_channels_dvr_guide import build as build_guide
from tests.test_channels_dvr_guide import golden_lineup, golden_stations

BOTH = {"guide": "gn", "tv_source": "OctopusTV", "radio_source": "OctopusRadio"}


def build(data: dict | None = None, env: dict | None = None) -> ChannelsDVRPublisher:
    sect = Section("publishers[0]", {"type": "channels_dvr", **(BOTH if data is None else data)}, Path("."), env or {})
    sect.name = "dvr"
    publisher: ChannelsDVRPublisher = registry.build("publisher", sect)
    return publisher


# What the user configured in Channels DVR; runs must put all of it back untouched
USER_SETTINGS = {
    "name": "My Sat",
    "source": "Text",
    "text": "#EXTM3U\nold\n",
    "limit": "4",
    "numbering": "ignore",
    "start_number": "500",
    "logos": "m3u",
    "guide_preference": "xmltv",
}


def _get(url: str) -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.json.return_value = dict(USER_SETTINGS) if "/providers/m3u/sources/" in url else golden_stations()
    return resp


@pytest.fixture
def http_session():
    """The session ChannelsDVRClient.connect() gets: serves the golden station list and source settings."""
    session = MagicMock()
    session.get.side_effect = _get
    with patch("sat_to_ip.http.session", return_value=session):
        yield session


def put_settings(session: MagicMock) -> dict[str, dict]:
    """source id → settings PUT to it."""
    return {c.args[0].rsplit("/", 1)[1]: c.kwargs["json"] for c in session.put.call_args_list}


def pushed(session: MagicMock) -> dict[str, str]:
    """source id → M3U text sent to it."""
    return {c.args[0].rsplit("/", 1)[1]: c.kwargs["json"]["text"] for c in session.put.call_args_list}


def ch(n: int, name: str, kind: str = "tv") -> Channel:
    return Channel(f"k{n}", name, kind, f"http://rx/{n}", n)  # type: ignore[arg-type]


LINEUP = Lineup((ch(1, "BBC One"), ch(2, "Radio 4", "radio"), ch(3, "ITV1")))
GUIDE = GuideMatch(
    "gn",
    "gracenote",
    {"k1": GuideRef("BBC1", "BBC One HD", "101"), "k2": GuideRef("BBCR4", None, "201")},
    (LINEUP.channels[2],),
)


# config -----------------------------------------------------------------------------------------


def test_defaults():
    pub = build({"tv_source": "OctopusTV"})
    assert (pub.name, pub.url, pub.sid, pub.guide) == ("dvr", "http://localhost:8089", None, None)
    assert pub.accepts_guides == frozenset({"gracenote"})
    assert pub.sources == (Source("tv", "OctopusTV"),)
    assert pub.channel_numbers == "channels_dvr"


def test_all_options():
    pub = build(
        {
            "url": "https://dvr.lan:8089/",
            "sid_env": "DVR_SID",
            "guide": "gn",
            "tv_source": "TV1",
            "tv_display_name": "Octopus TV",
            "radio_source": "R1",
            "radio_display_name": "Octopus Radio",
            "channel_numbers": "list",
        },
        env={"DVR_SID": "abc"},
    )
    assert (pub.url, pub.sid, pub.guide) == ("https://dvr.lan:8089", "abc", "gn")
    assert "abc" not in repr(pub)
    assert pub.sources == (Source("tv", "TV1", "Octopus TV"), Source("radio", "R1", "Octopus Radio"))
    assert pub.channel_numbers == "list"


@pytest.mark.parametrize(
    "data, key",
    [
        ({}, "publishers[0].tv_source"),
        ({"tv_source": ""}, "publishers[0].tv_source"),
        ({"radio_display_name": "R"}, "publishers[0].radio_display_name"),
        ({"tv_source": "T", "url": "localhost"}, "publishers[0].url"),
        ({"tv_source": "T", "guide": 1}, "publishers[0].guide"),
        ({"tv_source": 7}, "publishers[0].tv_source"),
        ({"tv_source": "T", "channel_numbers": "receiver"}, "publishers[0].channel_numbers: must be one of"),
        ({"tv_source": "T", "channel_numbers": 1}, "publishers[0].channel_numbers"),
        ({"tv_source": "T", "tv_sorce": "x"}, "publishers[0]: unknown key(s) tv_sorce"),
        ({"tv_source": "S", "radio_source": "S"}, "publishers[0].radio_source: must differ from tv_source"),
    ],
)
def test_config_errors_name_their_key(data, key):
    with pytest.raises(ConfigError) as err:
        build(data)
    assert str(err.value).startswith(key)


def test_from_config_does_no_io():
    with patch("sat_to_ip.http.session") as make:
        pub = build()
    make.assert_not_called()
    assert "client" not in vars(pub)


# publish ----------------------------------------------------------------------------------------


def test_publishes_matched_channels_per_kind_with_guide_attributes(http_session):
    result = build().publish(LINEUP, GUIDE, dry_run=False)
    assert pushed(http_session) == {
        "OctopusTV": '#EXTM3U\n#EXTINF:-1 channel-id="BBC1" tvc-guide-stationid="101" tvg-name="BBC One HD",BBC One\n'
        "http://rx/1\n",
        "OctopusRadio": '#EXTM3U\n#EXTINF:-1 channel-id="BBCR4" tvc-guide-stationid="201" tvg-name="Radio 4",Radio 4\n'
        "http://rx/2\n",
    }
    # everything the user set in Channels is put back; only the playlist changes
    for settings in put_settings(http_session).values():
        assert settings == {**USER_SETTINGS, "text": settings["text"]}
    assert result.channels == 2
    assert result.detail == "tv: 1 → OctopusTV; radio: 1 → OctopusRadio"


def test_without_guide_publishes_every_channel_plain(http_session):
    result = build({"tv_source": "T", "tv_display_name": "My TV"}).publish(LINEUP, None, dry_run=False)
    assert pushed(http_session) == {"T": "#EXTM3U\n#EXTINF:-1,BBC One\nhttp://rx/1\n#EXTINF:-1,ITV1\nhttp://rx/3\n"}
    assert put_settings(http_session)["T"]["name"] == "My TV"  # an explicit display name still wins
    assert result.channels == 2 and result.detail == "tv: 2 → T"


def test_each_write_is_followed_by_a_refresh_after_reading_every_source(http_session):
    build().publish(LINEUP, GUIDE, dry_run=False)
    calls = [(name, args[0].removeprefix("http://localhost:8089")) for name, args, _ in http_session.method_calls]
    assert calls == [
        ("get", "/providers/m3u/sources/OctopusTV"),
        ("get", "/providers/m3u/sources/OctopusRadio"),
        ("put", "/providers/m3u/sources/OctopusTV"),
        ("post", "/providers/m3u/sources/OctopusTV/refresh"),
        ("put", "/providers/m3u/sources/OctopusRadio"),
        ("post", "/providers/m3u/sources/OctopusRadio/refresh"),
    ]


def test_channel_numbers_list_renders_numbers_and_prefers_them(http_session):
    build({**BOTH, "channel_numbers": "list"}).publish(LINEUP, GUIDE, dry_run=False)
    tv = put_settings(http_session)["OctopusTV"]
    assert tv["text"] == (
        '#EXTM3U\n#EXTINF:-1 channel-id="BBC1" tvc-guide-stationid="101" tvg-name="BBC One HD" channel-number="1",'
        "BBC One\nhttp://rx/1\n"
    )
    assert tv == {**USER_SETTINGS, "text": tv["text"], "numbering": ""}


def test_channel_numbers_list_without_guide(http_session):
    build({"tv_source": "T", "channel_numbers": "list"}).publish(LINEUP, None, dry_run=False)
    assert pushed(http_session)["T"] == (
        '#EXTM3U\n#EXTINF:-1 channel-number="1",BBC One\nhttp://rx/1\n#EXTINF:-1 channel-number="3",ITV1\nhttp://rx/3\n'
    )


def test_missing_source_fails_before_anything_is_written(http_session):
    def get(url: str) -> MagicMock:
        resp = _get(url)
        if url.endswith("/OctopusRadio"):
            resp.status_code = 404
        return resp

    http_session.get.side_effect = get
    with pytest.raises(IntegrationError, match="source 'OctopusRadio' doesn't exist; create it in Channels DVR first"):
        build().publish(LINEUP, GUIDE, dry_run=False)
    http_session.put.assert_not_called()
    http_session.post.assert_not_called()


def test_sid_cookie_set_on_first_use(http_session):
    build({**BOTH, "sid_env": "SID"}, env={"SID": "abc"}).publish(LINEUP, GUIDE, dry_run=False)
    http_session.cookies.set.assert_called_once_with("SID", "abc", domain="localhost.local")


def test_dry_run_makes_no_write_call(http_session):
    result = assert_publisher_contract(build(), LINEUP, GUIDE)
    http_session.put.assert_not_called()
    http_session.post.assert_not_called()
    assert http_session.get.call_count == 2  # the sources were checked
    assert result.detail.endswith("(dry run; sources found)")


def test_contract_without_guide(http_session):
    assert_publisher_contract(build({"tv_source": "T", "radio_source": "R"}), LINEUP, None)
    http_session.put.assert_not_called()
    http_session.post.assert_not_called()


def test_refuses_to_empty_a_source_and_pushes_nothing(http_session):
    guide = GuideMatch("gn", "gracenote", {"k2": GuideRef("BBCR4", None, "201")}, (LINEUP.channels[0],))
    with pytest.raises(
        CheckError, match="matched none of the 2 tv channels; refusing to empty Channels DVR source 'OctopusTV'"
    ):
        build().publish(LINEUP, guide, dry_run=False)
    http_session.get.assert_not_called()
    http_session.put.assert_not_called()  # radio would have been fine, but nothing is half-published


def test_refuses_to_empty_a_source_in_dry_run_too(http_session):
    guide = GuideMatch("gn", "gracenote", {}, LINEUP.channels)
    with pytest.raises(CheckError):
        build().publish(LINEUP, guide, dry_run=True)


def test_empties_a_source_when_the_lineup_has_none_of_its_kind(http_session):
    tv_only = Lineup((ch(1, "BBC One"),))
    result = build().publish(tv_only, GUIDE, dry_run=False)
    assert pushed(http_session)["OctopusRadio"] == "#EXTM3U\n"
    assert result.channels == 1 and result.detail == "tv: 1 → OctopusTV; radio: 0 → OctopusRadio"


def test_http_error_is_integration_error(http_session):
    http_session.put.return_value.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    with pytest.raises(IntegrationError, match="can't update source 'OctopusTV'"):
        build().publish(LINEUP, GUIDE, dry_run=False)


def test_quotes_in_names_cannot_break_the_attribute(http_session):
    guide = GuideMatch("gn", "gracenote", {"k1": GuideRef("X", 'The "Best" TV', "1")})
    build({"tv_source": "T"}).publish(Lineup((ch(1, "Best"),)), guide, dry_run=False)
    assert "tvg-name=\"The 'Best' TV\"" in pushed(http_session)["T"]


# golden: same bytes as the legacy enrich_m3u_text --------------------------------------------------


def test_golden_matches_legacy_enrichment_byte_for_byte(http_session):
    """expected_*.m3u were produced once by the removed legacy_enrichment.enrich_m3u_text (same input)."""
    lineup = golden_lineup()
    match = build_guide().match(lineup)
    result = build().publish(lineup, match, dry_run=False)
    assert pushed(http_session) == {
        "OctopusTV": (GOLDEN / "expected_tv.m3u").read_text(),
        "OctopusRadio": (GOLDEN / "expected_radio.m3u").read_text(),
    }
    assert result.detail == "tv: 4 → OctopusTV; radio: 2 → OctopusRadio"


def test_pipeline_run_with_real_guide_and_publisher(http_session, tmp_path):
    config = Config(
        receiver=FakeReceiver(channels=list(golden_lineup())),
        guides={"gn": build_guide()},
        publishers=(build(),),
        checks=CheckSettings(min_guide_match=0.5),
        path=tmp_path / "c.toml",
    )
    report = pipeline.run(config, RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert report.exit_code == 0, report.summary()
    assert [(o.name, o.status) for o in report.outcomes] == [("publish:dvr", "ok")]
    assert report.outcomes[0].detail == "6 channels; tv: 4 → OctopusTV; radio: 2 → OctopusRadio"
    assert pushed(http_session)["OctopusTV"] == (GOLDEN / "expected_tv.m3u").read_text()
    station_gets = [c for c in http_session.get.call_args_list if "/dvr/guide/stations/" in c.args[0]]
    assert len(station_gets) == 1  # the guide ran once
