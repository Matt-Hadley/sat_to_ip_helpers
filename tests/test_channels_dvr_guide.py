import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip import registry
from sat_to_ip.channels_dvr.guide import GracenoteGuide
from sat_to_ip.config import Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.model import Channel, GuideRef, Lineup
from tests.contracts import assert_guide_contract

GOLDEN = Path(__file__).parent / "data" / "channels_dvr_golden"


def golden_lineup() -> Lineup:
    rows = json.loads((GOLDEN / "lineup.json").read_text())
    return Lineup(
        tuple(Channel(f"octopus:{i}", r["name"], r["kind"], r["url"], i) for i, r in enumerate(rows, start=1))
    )


def golden_stations() -> list[dict[str, str]]:
    return json.loads((GOLDEN / "stations.json").read_text())


def section(data: dict, path: str = "guides.gn", name: str = "gn", env: dict | None = None, base_dir=Path(".")):
    return Section(path, {"type": "gracenote", **data}, base_dir, env or {}, name)


def build(data: dict | None = None, **kwargs) -> GracenoteGuide:
    guide: GracenoteGuide = registry.build("guide", section(data or {}, **kwargs))
    return guide


@pytest.fixture
def http_session():
    """The session ChannelsDVRClient.connect() gets, serving the golden station list."""
    session = MagicMock()
    session.get.return_value.json.return_value = golden_stations()
    with patch("sat_to_ip.http.session", return_value=session):
        yield session


def _names(channels) -> list[str]:
    return [ch.name for ch in channels]


# config -----------------------------------------------------------------------------------------


def test_defaults():
    guide = build()
    assert (guide.name, guide.kind) == ("gn", "gracenote")
    assert guide.url == "http://localhost:8089"
    assert guide.sid is None
    assert guide.station_list == "GBR-1000193-DEFAULT"
    assert guide.region == "uk" and guide.extra_mappings == {}


def test_all_options(tmp_path):
    guide = build(
        {
            "url": "http://dvr:8089/",
            "sid_env": "DVR_SID",
            "station_list": "GBR-X",
            "mappings": "UK",
            "extra_mappings": {"Mystery TV": "QUEST"},
        },
        env={"DVR_SID": "s3cret"},
        base_dir=tmp_path,
    )
    assert (guide.url, guide.sid, guide.station_list, guide.region) == ("http://dvr:8089", "s3cret", "GBR-X", "UK")
    assert "s3cret" not in repr(guide)
    assert guide.extra_mappings == {"Mystery TV": "QUEST"}


def test_empty_mappings_means_no_builtin_region():
    assert build({"mappings": ""}).region is None


@pytest.mark.parametrize(
    "data, key",
    [
        ({"url": "dvr:8089"}, "guides.gn.url"),
        ({"station_list": 5}, "guides.gn.station_list"),
        ({"mappings": "atlantis"}, "guides.gn.mappings"),
        ({"extra_mappings": {"A": 1}}, "guides.gn.extra_mappings"),
        ({"mapings": "uk"}, "guides.gn: unknown key(s) mapings"),
    ],
)
def test_config_errors_name_their_key(tmp_path, data, key):
    with pytest.raises(ConfigError) as err:
        build(data, base_dir=tmp_path)
    assert str(err.value).startswith(key)


def test_unset_sid_variable_means_no_login():
    assert build({"sid_env": "MISSING"}).sid is None


def test_from_config_does_no_io():
    with patch("sat_to_ip.http.session") as make:
        guide = build()
    make.assert_not_called()
    assert "client" not in vars(guide)  # connects on first match()


# matching ---------------------------------------------------------------------------------------


def test_match_fetches_the_configured_station_list(http_session):
    build({"url": "http://dvr:1234", "station_list": "GBR-X"}).match(golden_lineup())
    assert http_session.get.call_args[0][0] == "http://dvr:1234/dvr/guide/stations/GBR-X"


def test_match_precedence_and_refs(http_session):
    lineup = golden_lineup()
    match = assert_guide_contract(build(), lineup)
    by_name = {ch.name: match.refs.get(ch.key) for ch in lineup}
    # explicit built-in mapping (5 HD → CH5HD), though the name alone wouldn't match
    assert by_name["5 HD"] == GuideRef("CH5HD", "Channel 5 HD", "10005")
    # normalised Gracenote name, and call sign
    assert by_name["BBC Radio 4"] == GuideRef("BBCR4", "BBC Radio 4", "20004")
    assert by_name["ITV1GHD"] == GuideRef("ITV1GHD", "ITV1 Granada HD", "10002")
    # a station with no Gracenote name has no display name
    assert by_name["Quest"] == GuideRef("QUEST", None, "10006")
    # explicit mapping to a call sign not in the list (Channel 4 HD → C4HD) is unmatched, as before,
    # even though the name would have matched CH4HD
    assert _names(match.unmatched) == ["Channel 4 HD", "Radio 4", "5 USA", "Mystery TV", "Pirate FM"]
    assert match.xmltv is None


def test_extra_mappings_beat_builtin_and_fuzzy(http_session):
    match = build({"extra_mappings": {"channel 4 hd": "CH4HD", "Mystery TV": "QUEST"}}).match(golden_lineup())
    assert match.refs["octopus:2"].guide_id == "CH4HD"
    assert match.refs["octopus:8"].guide_id == "QUEST"


def test_no_mappings_matches_by_name_only(http_session):
    match = build({"mappings": ""}).match(golden_lineup())
    assert match.refs["octopus:2"].guide_id == "CH4HD"  # no C4HD override now
    assert "octopus:5" not in match.refs  # "5 HD" needs the mapping


def test_later_station_wins_and_entries_without_call_sign_are_ignored(http_session):
    http_session.get.return_value.json.return_value = [
        {"callSign": "OLD", "stationId": "1", "name": "Same Name"},
        {"callSign": "NEW", "stationId": "2", "name": "Same Name"},
        {"callSign": "", "stationId": "3", "name": "Orphan"},
        {"callSign": " PAD ", "stationId": None, "name": None},
    ]
    lineup = Lineup(
        (
            Channel("k1", "same name", "tv", "u1", 1),
            Channel("k2", "Orphan", "tv", "u2", 2),
            Channel("k3", "pad", "tv", "u3", 3),
        )
    )
    match = build({"mappings": ""}).match(lineup)
    assert match.refs == {"k1": GuideRef("NEW", "Same Name", "2"), "k3": GuideRef("PAD", None, "")}
    assert _names(match.unmatched) == ["Orphan"]


def test_empty_lineup(http_session):
    match = assert_guide_contract(build(), Lineup())
    assert match.refs == {} and match.unmatched == ()


def test_sid_is_sent_as_cookie(http_session):
    build({"sid_env": "SID"}, env={"SID": "abc"}).match(Lineup())
    http_session.cookies.set.assert_called_once_with("SID", "abc", domain="localhost.local")


def test_http_error_is_integration_error(http_session):
    http_session.get.side_effect = requests.ConnectionError("Connection refused")
    with pytest.raises(IntegrationError, match="station list 'GBR-1000193-DEFAULT'.*refused"):
        build().match(golden_lineup())
