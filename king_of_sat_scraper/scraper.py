import logging
from typing import List

from bs4 import BeautifulSoup

from king_of_sat_scraper.transponder import Transponder


class KingOfSatScraper:
    def __init__(self, html_content: str):
        """
        Initializes the scraper with HTML content.
        :param html_content: HTML content as a string
        """
        self.soup = BeautifulSoup(html_content, "html.parser")
        self.transponders = []

    def _validate_header_table(self) -> bool:
        expected_columns = [
            "Pos",
            "Satellite",
            "Frequency",
            "Pol",
            "Txp",
            "Coverage area",
            "Standard",
            "Modulation",
            "SR/FEC",
            "Network, Bitrate",
            "NID",
            "TID",
        ]
        header_table = self.soup.find("table", class_="frq")
        if not header_table:
            logging.error("❌ No header table found.")
            return False

        header_row = header_table.find("tr")
        if not header_row:
            logging.error("❌ Header row missing.")
            return False

        actual_columns = [td.get_text(strip=True) for td in header_row.find_all("td")]

        # Strip out columns that contain only images or links
        actual_columns = [
            col for col in actual_columns if not (col == "" or col.lower() == "[img]" or col.startswith("<a"))
        ]

        if actual_columns != expected_columns:
            logging.error("❌ Header columns mismatch.\nExpected: %s\nFound: %s", expected_columns, actual_columns)
            return False

        logging.info("✅ Header table validated successfully.")
        return True

    def parse_transponders(self) -> List[Transponder]:
        """
        Parse transponder information from the HTML content.
        :return: List of Transponder objects
        """
        if not self._validate_header_table():
            logging.error("❌ HTML header structure is invalid. Aborting parse.")
            return []

        tables = self.soup.find_all("table", class_="frq")
        if len(tables) <= 1:
            logging.error("❌ No transponder data tables found after header.")
            return []

        transponders = []

        for i, table in enumerate(tables[1:], start=1):
            row = table.find("tr")
            if not row:
                logging.warning(f"⚠️ Table {i} has no <tr> row.")
                continue

            cols = row.find_all("td")
            if len(cols) < 12:
                logging.warning(f"⚠️ Skipping malformed transponder table at index {i}.")
                continue

            try:
                position = cols[0].get_text(strip=True)

                satellite_name_tag = cols[1].find("a")
                satellite = satellite_name_tag.get_text(strip=True) if satellite_name_tag else ""

                freq = float(cols[2].get_text(strip=True).replace(",", "").replace("MHz", "").strip())
                pol = cols[3].get_text(strip=True).upper()
                t_id = int(cols[4].get_text(strip=True))
                beam = cols[5].get_text(strip=True)
                system = cols[6].get_text(strip=True)
                mod = cols[7].get_text(strip=True)

                # Usually "<a>27500</a> <a>5/6</a>"; fall back to plain text. No SR raises and skips the row.
                sr_fec = [a.get_text(strip=True) for a in cols[8].find_all("a")] or cols[8].get_text(
                    " ", strip=True
                ).split()
                symbol_rate = int(sr_fec[0])
                fec = sr_fec[1] if len(sr_fec) > 1 else ""

                bitrate = cols[9].get_text(strip=True)
                nid = int(cols[10].get_text(strip=True))
                tid = int(cols[11].get_text(strip=True))

                transponder = Transponder(
                    position=position,
                    satellite=satellite,
                    frequency=freq,
                    polarization=pol,
                    transponder_id=t_id,
                    beam=beam,
                    system=system,
                    modulation=mod,
                    symbol_rate=symbol_rate,
                    fec=fec,
                    network_bitrate=bitrate,
                    nid=nid,
                    tid=tid,
                )

                transponders.append(transponder)
                logging.debug(f"✅ Parsed Transponder {i}: {transponder}")

            except Exception as e:
                logging.error(f"❌ Error parsing transponder {i}: {e}")

        logging.info(f"🎯 Parsed {len(transponders)} transponders total.")
        return transponders
