from unittest.mock import MagicMock, patch

import pytest

from king_of_sat_scraper.client import KingOfSatClient
from king_of_sat_scraper.transponder import Transponder


def _mock_session(html: str = "<html></html>", status_code: int = 200) -> MagicMock:
    session = MagicMock()
    resp = MagicMock()
    resp.text = html
    resp.status_code = status_code
    session.get.return_value = resp
    return session


# ---------------------------------------------------------------------------
# _build_url
# ---------------------------------------------------------------------------


def test_build_url_includes_position():
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", MagicMock())
    url = client._build_url("28.2E", "Clear", "eng")
    assert "pos=28.2E" in url


def test_build_url_includes_filter():
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", MagicMock())
    url = client._build_url("28.2E", "All", "eng")
    assert "filtre=All" in url


def test_build_url_includes_language():
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", MagicMock())
    url = client._build_url("28.2E", "Clear", "fra")
    assert "cl=fra" in url


# ---------------------------------------------------------------------------
# fetch_transponders
# ---------------------------------------------------------------------------


def test_fetch_transponders_calls_get_with_correct_url():
    session = _mock_session()
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", session)
    with patch("king_of_sat_scraper.client.KingOfSatScraper") as mock_scraper:
        mock_scraper.return_value.parse_transponders.return_value = []
        client.fetch_transponders("28.2E", channel_filter="Clear", cl="eng")
    url_called = session.get.call_args[0][0]
    assert "pos=28.2E" in url_called
    assert "filtre=Clear" in url_called


def test_fetch_transponders_raises_on_http_error():
    session = MagicMock()
    session.get.return_value.raise_for_status.side_effect = Exception("404")
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", session)
    with pytest.raises(Exception, match="404"):
        client.fetch_transponders("28.2E")


def test_fetch_transponders_returns_parsed_list():
    session = _mock_session(html="<html>some html</html>")
    fake_transponder = MagicMock(spec=Transponder)
    client = KingOfSatClient("https://en.kingofsat.net/freqs.php", session)
    with patch("king_of_sat_scraper.client.KingOfSatScraper") as mock_scraper:
        mock_scraper.return_value.parse_transponders.return_value = [fake_transponder]
        result = client.fetch_transponders("28.2E")
    assert result == [fake_transponder]


def test_create_classmethod_returns_client_instance():
    client = KingOfSatClient.create()
    assert isinstance(client, KingOfSatClient)
    assert "kingofsat" in client.base_url
