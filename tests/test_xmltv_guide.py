import gzip
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip.config import Section
from sat_to_ip.exceptions import ConfigError, IntegrationError
from sat_to_ip.model import Lineup
from sat_to_ip.xmltv.guide import XmltvGuide, from_config
from tests.contracts import assert_guide_contract
from tests.fakes import channel

FEED = Path(__file__).parent / "data" / "xmltv_feed.xml"
URL = "https://example.org/epg.xml"


def _section(data, base=Path("/base")):
    return Section("guides.freeview", data, base, {}, "freeview")


def _lineup(*names):
    return Lineup(tuple(channel(i + 1, name) for i, name in enumerate(names)))


def _session(body: bytes, chunk=7):
    sess = MagicMock()
    sess.get.return_value.iter_content.return_value = [body[i : i + chunk] for i in range(0, len(body), chunk)]
    return sess


def _match_url(body, lineup, **kw):
    sess = _session(body)
    with patch("sat_to_ip.xmltv.guide.http.session", return_value=sess):
        return XmltvGuide("freeview", url=URL, **kw).match(lineup), sess


def _ids(match):
    return {k: r.guide_id for k, r in match.refs.items()}


# Config ------------------------------------------------------------------------------------------


def test_from_config_url():
    guide = from_config(_section({"url": URL, "max_bytes": 10}))
    assert (guide.name, guide.kind, guide.url, guide.path, guide.max_bytes) == ("freeview", "xmltv", URL, None, 10)
    assert guide.mappings == {}


def test_from_config_path_resolves_against_config_dir():
    guide = from_config(_section({"path": "epg.xml"}, base=Path("/cfg")))
    assert guide.path == Path("/cfg/epg.xml").resolve() and guide.url is None


@pytest.mark.parametrize(
    "data, key",
    [
        ({}, "url"),
        ({"url": URL, "path": "x.xml"}, "url"),
        ({"url": "ftp://x"}, "url"),
        ({"path": 3}, "path"),
        ({"url": URL, "max_bytes": 0}, "max_bytes"),
        ({"url": URL, "mappings": "atlantis"}, "mappings"),
        ({"url": URL, "mappings_file": "/nope/m.csv"}, "mappings_file"),
    ],
)
def test_from_config_errors_name_their_key(data, key):
    with pytest.raises(ConfigError, match=rf"^guides\.freeview\.{key}: "):
        from_config(_section(data))


def test_from_config_does_no_io():
    with patch("sat_to_ip.xmltv.guide.http.session") as session:
        from_config(_section({"url": URL}))
        from_config(_section({"path": "/does/not/exist.xml"}))
    session.assert_not_called()


def test_from_config_mappings_builtin_and_file_override(tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text("Name,GuideId\nHeart,Mine.uk\nExtra One,Extra.uk\n")
    guide = from_config(_section({"url": URL, "mappings": "freeview-epg", "mappings_file": str(csv)}))
    assert guide.mappings["heart"] == "Mine.uk"
    assert guide.mappings["extra one"] == "Extra.uk"
    assert guide.mappings["bbc r6 music"] == "BBCR6M.uk"


# Matching ----------------------------------------------------------------------------------------


def test_matching_precedence_and_unmatched():
    lineup = _lineup("BBC One HD", "BBC 1", "Film4", "ITV2", "Mapped Name", "Quest", "Missing", "Ghost")
    match = XmltvGuide("freeview", path=FEED, mappings={"mapped name": "Target.uk", "ghost": "Gone.uk"}).match(lineup)
    assert _ids(match) == {
        "fake:1": "BBCOne.uk",  # display-name
        "fake:2": "BBCOne.uk",  # second display-name, same id as fake:1
        "fake:3": "Other.uk",  # display-name "Film4" beats id "Film4.uk"
        "fake:4": "ITV2.uk",  # id without its suffix
        "fake:5": "Target.uk",  # mapping beats display-name "Mapped Name" (Decoy.uk)
    }
    # "Quest" is ambiguous (Quest.uk / Quest.ie); a mapping to an id not in the feed is unmatched
    assert [c.name for c in match.unmatched] == ["Quest", "Missing", "Ghost"]
    assert match.refs["fake:1"].display_name == "BBC One HD" and match.refs["fake:1"].station_id is None


def test_filtered_xmltv_contains_only_matched_channels_and_programmes():
    match = XmltvGuide("freeview", path=FEED).match(_lineup("BBC One HD", "BBC 1", "ITV2"))
    assert match.xmltv is not None and match.xmltv.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    root = ET.fromstring(match.xmltv)
    assert root.tag == "tv" and root.attrib == {
        "generator-info-name": "fixture",
        "source-info-url": "https://example.org/epg",
    }
    assert [c.get("id") for c in root.findall("channel")] == ["BBCOne.uk", "ITV2.uk"]
    assert [(p.get("channel"), p.findtext("title")) for p in root.findall("programme")] == [
        ("BBCOne.uk", "Breakfast"),
        ("ITV2.uk", "Cartoons"),
        ("BBCOne.uk", "Morning Live"),
    ]
    assert [child.tag for child in root] == ["channel", "channel", "programme", "programme", "programme"]
    bbc = root.find("channel")
    assert bbc is not None and bbc.find("icon") is not None and len(bbc.findall("display-name")) == 2
    assert root.find("programme/desc").text == "News at the Café."  # type: ignore[union-attr]
    assert root.find("programme/episode-num").get("system") == "xmltv_ns"  # type: ignore[union-attr]


def test_contract():
    match = assert_guide_contract(XmltvGuide("freeview", path=FEED), _lineup("BBC One HD", "Nope", "ITV2"))
    assert match.match_ratio == pytest.approx(2 / 3)


def test_empty_lineup_and_no_programmes():
    feed = b'<tv a="1"><channel id="X.uk"><display-name>X</display-name></channel></tv>'
    match, _ = _match_url(feed, _lineup("X"))
    assert _ids(match) == {"fake:1": "X.uk"}
    assert ET.fromstring(match.xmltv).attrib == {"a": "1"}  # type: ignore[arg-type]
    match, _ = _match_url(feed, Lineup())
    assert match.refs == {} and [c.tag for c in ET.fromstring(match.xmltv)] == []  # type: ignore[arg-type]


def test_channel_after_programmes_is_ignored():
    feed = (
        b"<tv><channel id='A.uk'><display-name>A</display-name></channel>"
        b"<programme channel='A.uk' start='1'><title>a</title></programme>"
        b"<channel id='B.uk'><display-name>B</display-name></channel></tv>"
    )
    match, _ = _match_url(feed, _lineup("A", "B"))
    assert _ids(match) == {"fake:1": "A.uk"}


def test_channel_without_display_name():
    match, _ = _match_url(b"<tv><channel id='Bare.uk'/></tv>", _lineup("Bare"))
    assert match.refs["fake:1"].display_name is None


# Loading -----------------------------------------------------------------------------------------


def test_downloads_streamed_with_size_cap():
    body = FEED.read_bytes()
    match, sess = _match_url(body, _lineup("BBC One HD"), max_bytes=len(body))
    assert _ids(match) == {"fake:1": "BBCOne.uk"}
    assert sess.get.call_args.args == (URL,) and sess.get.call_args.kwargs["stream"] is True
    with pytest.raises(IntegrationError, match="larger than max_bytes"):
        _match_url(body, _lineup("BBC One HD"), max_bytes=len(body) - 1)


def test_gzip_feed():
    match, _ = _match_url(gzip.compress(FEED.read_bytes()), _lineup("BBC One HD"))
    assert _ids(match) == {"fake:1": "BBCOne.uk"}


def test_gzip_decompressed_size_is_capped():
    body = gzip.compress(FEED.read_bytes())
    assert len(body) < 1500 < len(FEED.read_bytes())
    with pytest.raises(IntegrationError, match="larger than max_bytes"):
        _match_url(body, _lineup("BBC One HD"), max_bytes=1500)


def test_corrupt_gzip():
    with pytest.raises(IntegrationError, match="corrupt gzip"):
        _match_url(gzip.compress(FEED.read_bytes())[:40], _lineup("BBC One HD"))


@pytest.mark.parametrize(
    "error", [requests.HTTPError("404 Client Error"), requests.Timeout("read timed out"), requests.ConnectionError()]
)
def test_http_errors(error):
    sess = MagicMock()
    sess.get.return_value.raise_for_status.side_effect = error
    with patch("sat_to_ip.xmltv.guide.http.session", return_value=sess):
        with pytest.raises(IntegrationError, match=f"freeview: can't download {URL}"):
            XmltvGuide("freeview", url=URL).match(_lineup("A"))


def test_path_feed_and_missing_file(tmp_path):
    gz = tmp_path / "epg.xml.gz"
    gz.write_bytes(gzip.compress(FEED.read_bytes()))
    assert _ids(XmltvGuide("freeview", path=gz).match(_lineup("ITV2"))) == {"fake:1": "ITV2.uk"}
    with pytest.raises(IntegrationError, match="can't read"):
        XmltvGuide("freeview", path=tmp_path / "missing.xml").match(_lineup("A"))
    with pytest.raises(IntegrationError, match="larger than max_bytes"):
        XmltvGuide("freeview", path=gz, max_bytes=10).match(_lineup("A"))


@pytest.mark.parametrize("body", [b"<tv><channel id='a'>", b"not xml", b""])
def test_malformed_xml(body):
    with pytest.raises(IntegrationError, match="malformed XMLTV"):
        _match_url(body, _lineup("A"))


def test_not_xmltv_root():
    with pytest.raises(IntegrationError, match="not an XMLTV feed"):
        _match_url(b"<html><body/></html>", _lineup("A"))


def test_entity_expansion_attack_fails_cleanly():
    lol = "".join(f'<!ENTITY l{i} "{f"&l{i - 1};" * 10}">' for i in range(1, 10))
    body = f'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY l0 "lol">{lol}]><tv><channel id="&l9;"/></tv>'
    with pytest.raises(IntegrationError, match="malformed XMLTV"):
        _match_url(body.encode(), _lineup("A"))


def test_external_entity_is_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    body = (
        f'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY x SYSTEM "file://{secret}">]>'
        '<tv><channel id="X.uk"><display-name>&x;</display-name></channel></tv>'
    )
    with pytest.raises(IntegrationError, match="malformed XMLTV"):
        _match_url(body.encode(), _lineup("TOPSECRET"))
