import logging
import os

import pytest

from sat_to_ip.kingofsat.scraper import KingOfSatScraper
from sat_to_ip.kingofsat.transponder import Transponder

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "kingofsat_28.2E.html")


def load_html_content() -> str:
    """Saved KingOfSat 28.2°E page (Clear, eng), fetched 2026-10-29."""
    with open(FIXTURE, encoding="utf-8") as file:
        return file.read()


def _row(
    freq_pol: str = "10773.25 H",
    standard: str = '<span class="dvbs2">DVB-S2</span>',
    modulation: str = "8PSK",
    sr: str = "<a>23000</a>",
    summary: bool = True,
) -> str:
    """One transponder in the current KingOfSat layout."""
    html = (
        '<table class="frequencies-table frq kos-frequency-header"><tr class="frequency-row" data-frequency-id="1">'
        '<td class="pos">28.2&deg;E</td>'
        '<td><a class="bld">Astra 2E</a><img src="/x.gif"><span class="nbc">9</span></td>'
        f'<td class="bld">{freq_pol}</td><td><a>45</a></td><td><a>U.K.</a></td>'
        f"<td>{standard}</td><td>{modulation}</td><td>{sr}</td><td><a>3/4</a></td>"
        "<td>&nbsp;</td><td>2026-10-29</td></tr></table>"
    )
    if summary:
        html += (
            '<div><table class="frq kos-stream-summary"><tr><td class="stream-type">TS</td>'
            '<td class="stream-bitrate">50.1 Mb/s</td><td class="stream-network"></td>'
            "<td>NID 2 - TSID 2045</td><td>2023-02-21</td></tr></table></div>"
        )
    return html


def test_parse_fixture():
    transponders = KingOfSatScraper(load_html_content()).parse_transponders()

    assert len(transponders) == 35
    assert transponders[0] == Transponder(
        position="28.2°E",
        satellite="Astra 2E",
        frequency=10773.25,
        polarization="H",
        transponder_id=45,
        beam="U.K.",
        system="DVB-S2",
        modulation="8PSK",
        symbol_rate=23000,
        fec="3/4",
        network_bitrate="50.1 Mb/s",
        nid=2,
        tid=2045,
    )
    last = transponders[-1]
    assert (last.frequency, last.polarization, last.system, last.modulation, last.symbol_rate) == (
        11426.5,
        "H",
        "DVB-S",
        "QPSK",
        27500,
    )
    assert (last.nid, last.tid) == (59, 2315)


def test_fixture_logs_one_summary_and_no_warnings(caplog):
    with caplog.at_level(logging.DEBUG, logger="sat_to_ip.kingofsat.scraper"):
        KingOfSatScraper(load_html_content()).parse_transponders()
    assert [r.getMessage() for r in caplog.records] == ["Parsed 35 transponders, skipped 0 rows"]


def test_snippet_row_parses():
    [t] = KingOfSatScraper(_row()).parse_transponders()
    assert t.satellite == "Astra 2E"  # not "Astra 2E 9"
    assert (t.frequency, t.polarization, t.system, t.modulation, t.symbol_rate, t.fec) == (
        10773.25,
        "H",
        "DVB-S2",
        "8PSK",
        23000,
        "3/4",
    )
    assert (t.network_bitrate, t.nid, t.tid) == ("50.1 Mb/s", 2, 2045)


def test_circular_polarisation_accepted():
    [t] = KingOfSatScraper(_row(freq_pol="11000 l")).parse_transponders()
    assert t.polarization == "L"


def test_missing_summary_table_defaults():
    [t] = KingOfSatScraper(_row(summary=False)).parse_transponders()
    assert (t.network_bitrate, t.nid, t.tid) == ("", 0, 0)


def test_next_transponder_is_not_taken_as_summary():
    first, _ = KingOfSatScraper(_row(summary=False) + _row()).parse_transponders()
    assert (first.nid, first.tid) == (0, 0)


def test_summary_without_ids_or_bitrate():
    html = _row(summary=False) + '<table class="frq kos-stream-summary"><tr><td>TS</td></tr></table>'
    [t] = KingOfSatScraper(html).parse_transponders()
    assert (t.network_bitrate, t.nid, t.tid) == ("", 0, 0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"freq_pol": "10773.25 X"},
        {"freq_pol": "10773.25"},
        {"freq_pol": "abc H"},
        {"modulation": "8psk&pol=v"},
        {"modulation": ""},
        {"standard": "DVB S2"},
        {"standard": "DVB-S2&x=1"},
        {"sr": ""},
        {"sr": "<a>fast</a>"},
    ],
)
def test_invalid_row_skipped_with_one_debug_line(kwargs, caplog):
    html = _row(**kwargs) + _row()
    with caplog.at_level(logging.DEBUG, logger="sat_to_ip.kingofsat.scraper"):
        transponders = KingOfSatScraper(html).parse_transponders()
    assert len(transponders) == 1
    assert [r.levelno for r in caplog.records] == [logging.DEBUG, logging.INFO]
    assert caplog.records[-1].getMessage() == "Parsed 1 transponders, skipped 1 rows"


def test_short_row_skipped():
    html = '<table><tr class="frequency-row"><td>28.2E</td><td>Astra</td></tr></table>'
    assert KingOfSatScraper(html).parse_transponders() == []


def test_other_tables_ignored_silently(caplog):
    html = '<table class="frq"><tr><td>Pos</td></tr></table><table class="frq"><tr><td>x</td></tr></table>'
    with caplog.at_level(logging.DEBUG, logger="sat_to_ip.kingofsat.scraper"):
        assert KingOfSatScraper(html).parse_transponders() == []
    assert [r.getMessage() for r in caplog.records] == ["Parsed 0 transponders, skipped 0 rows"]


def test_transponder_rejects_bad_polarization():
    with pytest.raises(ValueError, match="Invalid polarization"):
        Transponder("28.2E", "Astra", 10773.0, "X", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "", 2, 2045)


def test_transponder_uppercases_polarization():
    t = Transponder("28.2E", "Astra", 10773.0, "h", 1, "UK", "DVB-S2", "8PSK", 23000, "3/4", "", 2, 2045)
    assert t.polarization == "H"
