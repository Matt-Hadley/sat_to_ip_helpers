import logging
import re
from datetime import datetime
from typing import List

from bs4 import BeautifulSoup

from king_of_sat_scraper.channel import Channel
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
            "Frequence",
            "Pol",
            "Txp",
            "Beam",
            "Standard",
            "Modulation",
            "SR/FEC",
            "Network, bitrate",
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

                sr_fec_links = cols[8].find_all("a")
                symbol_rate = int(sr_fec_links[0].get_text(strip=True)) if len(sr_fec_links) > 0 else 0.0
                fec = sr_fec_links[1].get_text(strip=True) if len(sr_fec_links) > 1 else ""

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

    def parse_apids(self, apid_cell) -> dict:
        apids = {}
        raw_apids = []

        # Break into parts based on <br> or newlines
        parts = list(filter(None, re.split(r"<br\s*/?>|\n", str(apid_cell))))

        for part in parts:
            text = BeautifulSoup(part, "html.parser").get_text(strip=True)
            match = re.match(r"(\d+)\s*([a-zA-Z]+)?", text)
            if match:
                pid = int(match.group(1))
                tag = match.group(2).lower() if match.group(2) else None
                if tag and tag != "nar":
                    apids[tag] = pid
                elif tag == "nar":
                    apids["__nar_temp__"] = pid
                else:
                    raw_apids.append(pid)

        # Get any additional languages from <font> tags
        lang_tags = apid_cell.find_all("font")
        languages = [tag.get_text(strip=True).lower() for tag in lang_tags]

        for pid, lang in zip(raw_apids, languages):
            apids[lang] = pid

        # Move "nar" to the end
        if "__nar_temp__" in apids:
            nar_pid = apids.pop("__nar_temp__")
            apids["nar"] = nar_pid

        return apids

    def parse_channels(self) -> List[Channel]:
        channels = []

        for table in self.soup.find_all("table", class_="fl"):
            for row in table.find_all("tr", bgcolor="white"):
                try:
                    cols = row.find_all("td")

                    # First td holds the channel type (v, a, f, d)
                    channel_type_td = cols[0]
                    class_attr = channel_type_td.get("class", [])
                    # "v", "a", etc.
                    channel_type = class_attr[0] if class_attr else None

                    name_tag = cols[2].find("a", class_="A3")
                    name = name_tag.get_text(strip=True) if name_tag else cols[2].get_text(strip=True)

                    country = cols[3].get_text(strip=True) or None
                    genre = cols[4].get_text(strip=True) or None

                    # Some rows don't have links in packages
                    packages = []
                    pkg_links = cols[5].find_all("a")
                    if pkg_links:
                        packages = [a.get_text(strip=True) for a in pkg_links]

                    encryption = cols[6].get_text(strip=True)
                    sid = int(cols[7].get_text(strip=True))

                    # VPID
                    vpid_text = cols[8].get_text(strip=True)
                    vpid_match = re.search(r"\d+", vpid_text) if vpid_text else None
                    vpid = int(vpid_match.group()) if vpid_match else None

                    apids = self.parse_apids(cols[9])
                    pmt = int(cols[10].get_text(strip=True)) if cols[10].get_text(strip=True).isdigit() else None
                    pcr = int(cols[11].get_text(strip=True)) if cols[11].get_text(strip=True).isdigit() else None
                    txt_col = cols[12].get_text(strip=True)
                    txt = int(txt_col) if txt_col.isdigit() else None

                    # Date
                    date_str = cols[13].get_text(strip=True)
                    date_str = re.sub(r"[^0-9-]", "", date_str)
                    last_updated = datetime.strptime(date_str, "%Y-%m-%d")

                    channel = Channel(
                        channel_type=channel_type,
                        name=name,
                        country=country,
                        category=genre,
                        packages=packages,
                        encryption=encryption,
                        sid=sid,
                        vpid=vpid,
                        apids=apids,
                        pmt=pmt,
                        pcr=pcr,
                        txt=txt,
                        last_updated=last_updated,
                    )

                    channels.append(channel)

                except Exception as e:
                    logging.warning(f"⚠️ Error parsing channel row: {e}. Row content: {row.get_text(strip=True)}")

        logging.info(f"📺 Parsed {len(channels)} channels.")
        return channels
