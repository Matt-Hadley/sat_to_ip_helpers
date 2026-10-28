import pytest

from king_of_sat_scraper.scraper import KingOfSatScraper
from king_of_sat_scraper.tests.test_scraper import VALID_HEADER_HTML
from king_of_sat_scraper.transponder import Transponder


def test_no_header_table_returns_empty():
    assert KingOfSatScraper("<html></html>").parse_transponders() == []


def test_header_table_without_row_is_invalid():
    assert KingOfSatScraper('<table class="frq"></table>')._validate_header_table() is False


def test_header_only_returns_empty():
    assert KingOfSatScraper(VALID_HEADER_HTML).parse_transponders() == []


def test_data_table_without_row_is_skipped():
    assert KingOfSatScraper(VALID_HEADER_HTML + '<table class="frq"></table>').parse_transponders() == []


def test_row_without_symbol_rate_is_skipped():
    cells = ["28.2°E", "<a>Astra 2F</a>", "11097", "V", "1", "UK", "DVB-S2", "8PSK", "", "x", "2", "2001"]
    html = VALID_HEADER_HTML + '<table class="frq"><tr>' + "".join(f"<td>{c}</td>" for c in cells) + "</tr></table>"
    assert KingOfSatScraper(html).parse_transponders() == []


def test_transponder_rejects_bad_polarization():
    with pytest.raises(ValueError, match="Invalid polarization"):
        Transponder("28.2E", "Astra", 10773.0, "X", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "", 2, 2045)


def test_transponder_uppercases_polarization():
    t = Transponder("28.2E", "Astra", 10773.0, "h", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "", 2, 2045)
    assert t.polarization == "H"
