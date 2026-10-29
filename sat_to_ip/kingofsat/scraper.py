import logging
import re

from bs4 import BeautifulSoup, Tag

from sat_to_ip.kingofsat.transponder import Transponder

logger = logging.getLogger(__name__)

# These strings end up in the tuning request sent to the receiver, so only accept known shapes.
_POLARIZATIONS = {"H", "V", "L", "R"}
_SYSTEM_RE = re.compile(r"^[A-Za-z0-9-]+$")
_MODULATION_RE = re.compile(r"^[0-9A-Z]+$")
_NID_TID_RE = re.compile(r"NID\s*(\d+)\s*-\s*TSID\s*(\d+)")


def _text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)


class KingOfSatScraper:
    def __init__(self, html_content: str):
        """
        Initializes the scraper with HTML content.
        :param html_content: HTML content as a string
        """
        self.soup = BeautifulSoup(html_content, "html.parser")

    def parse_transponders(self) -> list[Transponder]:
        """
        Parse transponder information from the HTML content.

        Each transponder is a ``tr.frequency-row`` (11 cells), optionally followed by a
        ``table.kos-stream-summary`` holding bitrate and NID/TSID.
        :return: List of Transponder objects
        """
        transponders = []
        skipped = 0
        for row in self.soup.select("tr.frequency-row"):
            try:
                transponders.append(self._parse_row(row))
            except (ValueError, IndexError) as e:
                skipped += 1
                logger.debug("Skipping transponder row %s: %s", row.get("data-frequency-id"), e)
        logger.info("Parsed %d transponders, skipped %d rows", len(transponders), skipped)
        return transponders

    @staticmethod
    def _parse_row(row: Tag) -> Transponder:
        cols = row.find_all("td", recursive=False)
        if len(cols) < 11:
            raise ValueError(f"expected 11 cells, got {len(cols)}")

        freq_text, _, pol = _text(cols[2]).rpartition(" ")
        pol = pol.upper()
        system = _text(cols[5])
        modulation = _text(cols[6])
        if pol not in _POLARIZATIONS:
            raise ValueError(f"invalid polarization {pol!r}")
        if not _SYSTEM_RE.match(system):
            raise ValueError(f"invalid standard {system!r}")
        if not _MODULATION_RE.match(modulation):
            raise ValueError(f"invalid modulation {modulation!r}")

        bitrate, nid, tid = "", 0, 0
        summary = row.find_next("table")
        if summary is not None and "kos-stream-summary" in (summary.get("class") or []):
            bitrate_cell = summary.find("td", class_="stream-bitrate")
            bitrate = _text(bitrate_cell) if bitrate_cell else ""
            ids = _NID_TID_RE.search(_text(summary))
            if ids:
                nid, tid = int(ids.group(1)), int(ids.group(2))

        satellite_tag = cols[1].find("a")
        return Transponder(
            position=_text(cols[0]),
            satellite=_text(satellite_tag) if satellite_tag else "",
            frequency=float(freq_text.replace(",", "")),
            polarization=pol,
            transponder_id=int(_text(cols[3]) or 0),
            beam=_text(cols[4]),
            system=system,
            modulation=modulation,
            symbol_rate=int(_text(cols[7])),
            fec=_text(cols[8]),
            network_bitrate=bitrate,
            nid=nid,
            tid=tid,
        )
