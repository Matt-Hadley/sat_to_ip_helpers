import logging
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


@pytest.fixture
def http_session():
    """The session ChannelsDVRClient.connect() gets: serves the golden station list, records PUTs."""
    session = MagicMock()
    session.get.return_value.json.return_value = golden_stations()
    with patch("sat_to_ip.http.session", return_value=session):
        yield session


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
    assert pub.sources == (Source("tv", "OctopusTV", "OctopusTV"),)


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
        },
        env={"DVR_SID": "abc"},
    )
    assert (pub.url, pub.sid, pub.guide) == ("https://dvr.lan:8089", "abc", "gn")
    assert pub.sources == (Source("tv", "TV1", "Octopus TV"), Source("radio", "R1", "Octopus Radio"))


@pytest.mark.parametrize(
    "data, key",
    [
        ({}, "publishers[0].tv_source"),
        ({"tv_source": ""}, "publishers[0].tv_source"),
        ({"radio_display_name": "R"}, "publishers[0].radio_display_name"),
        ({"tv_source": "T", "url": "localhost"}, "publishers[0].url"),
        ({"tv_source": "T", "guide": 1}, "publishers[0].guide"),
        ({"tv_source": 7}, "publishers[0].tv_source"),
        ({"tv_source": "T", "tv_sorce": "x"}, "publishers[0]: unknown key(s) tv_sorce"),
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
    names = {c.args[0].rsplit("/", 1)[1]: c.kwargs["json"]["name"] for c in http_session.put.call_args_list}
    assert names == {"OctopusTV": "OctopusTV", "OctopusRadio": "OctopusRadio"}
    assert result.target == "dvr" and result.channels == 2
    assert result.detail == "tv: 1 → OctopusTV; radio: 1 → OctopusRadio"


def test_without_guide_publishes_every_channel_plain(http_session):
    result = build({"tv_source": "T", "tv_display_name": "My TV"}).publish(LINEUP, None, dry_run=False)
    assert pushed(http_session) == {"T": "#EXTM3U\n#EXTINF:-1,BBC One\nhttp://rx/1\n#EXTINF:-1,ITV1\nhttp://rx/3\n"}
    assert http_session.put.call_args.kwargs["json"]["name"] == "My TV"
    assert result.channels == 2 and result.detail == "tv: 2 → T"


def test_sid_cookie_set_on_first_use(http_session):
    build({**BOTH, "sid_env": "SID"}, env={"SID": "abc"}).publish(LINEUP, GUIDE, dry_run=False)
    http_session.cookies.set.assert_called_once_with("SID", "abc")


def test_dry_run_makes_no_write_call(http_session):
    result = assert_publisher_contract(build(), LINEUP, GUIDE)
    http_session.put.assert_not_called()
    assert result.detail.endswith("(dry run)")


def test_contract_without_guide(http_session):
    assert_publisher_contract(build({"tv_source": "T", "radio_source": "R"}), LINEUP, None)
    http_session.put.assert_not_called()


def test_refuses_to_empty_a_source_and_pushes_nothing(http_session):
    guide = GuideMatch("gn", "gracenote", {"k2": GuideRef("BBCR4", None, "201")}, (LINEUP.channels[0],))
    with pytest.raises(CheckError, match="Refusing to empty Channels DVR source 'OctopusTV'.*2 tv channels"):
        build().publish(LINEUP, guide, dry_run=False)
    http_session.put.assert_not_called()  # radio would have been fine, but nothing is half-published


def test_refuses_to_empty_a_source_in_dry_run_too(http_session):
    guide = GuideMatch("gn", "gracenote", {}, LINEUP.channels)
    with pytest.raises(CheckError):
        build().publish(LINEUP, guide, dry_run=True)


def test_skips_a_source_when_the_lineup_has_none_of_its_kind(http_session, caplog):
    tv_only = Lineup((ch(1, "BBC One"),))
    with caplog.at_level(logging.INFO):
        result = build().publish(tv_only, GUIDE, dry_run=False)
    assert list(pushed(http_session)) == ["OctopusTV"]
    assert "No radio channels in the lineup; leaving OctopusRadio unchanged" in caplog.text
    assert result.channels == 1 and result.detail == "tv: 1 → OctopusTV"


def test_http_error_is_integration_error(http_session):
    http_session.put.return_value.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    with pytest.raises(IntegrationError, match="can't update source 'OctopusTV'"):
        build().publish(LINEUP, GUIDE, dry_run=False)


def test_quotes_in_names_cannot_break_the_attribute():
    guide = GuideMatch("gn", "gracenote", {"k1": GuideRef("X", 'The "Best" TV', "1")})
    with patch("sat_to_ip.http.session") as make:
        build({"tv_source": "T"}).publish(Lineup((ch(1, "Best"),)), guide, dry_run=False)
    assert "tvg-name=\"The 'Best' TV\"" in make.return_value.put.call_args.kwargs["json"]["text"]


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
        channels_file=None,
        path=tmp_path / "c.toml",
    )
    report = pipeline.run(config, RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert report.ok, report.summary()
    assert [(o.name, o.status) for o in report.outcomes] == [("publish:dvr", "ok")]
    assert report.outcomes[0].detail == "6 channels; tv: 4 → OctopusTV; radio: 2 → OctopusRadio"
    assert pushed(http_session)["OctopusTV"] == (GOLDEN / "expected_tv.m3u").read_text()
    http_session.get.assert_called_once()  # the guide ran once
