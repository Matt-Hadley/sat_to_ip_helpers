import logging
import math
import re

from bs4 import BeautifulSoup, Tag

from sat_to_ip.kingofsat.transponder import Transponder

logger = logging.getLogger(__name__)

# These strings end up in the tuning request sent to the receiver, so only accept known shapes.
_POLARIZATIONS = {"H", "V", "L", "R"}
_SYSTEM_RE = re.compile(r"^[A-Za-z0-9-]+$")
_MODULATION_RE = re.compile(r"^[0-9A-Z]+$")


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

        Each transponder is a ``tr.frequency-row`` with 11 cells.
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

        frequency = float(freq_text.replace(",", ""))
        if not math.isfinite(frequency) or frequency <= 0:
            raise ValueError(f"invalid frequency {freq_text!r}")

        satellite_tag = cols[1].find("a")
        return Transponder(
            satellite=_text(satellite_tag) if satellite_tag else "",
            frequency=frequency,
            polarization=pol,
            system=system,
            modulation=modulation,
            symbol_rate=int(_text(cols[7])),
        )
