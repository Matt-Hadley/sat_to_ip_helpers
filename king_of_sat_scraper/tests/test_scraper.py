import logging
import os
import unittest
from datetime import datetime

from bs4 import BeautifulSoup

from king_of_sat_scraper.scraper import KingOfSatScraper

# Suppress unnecessary debug info for unit tests
logging.basicConfig(level=logging.WARNING)

VALID_HEADER_HTML = """
        <table class="frq"><tr bgcolor="#999999">
        <td class="pos" dir="ltr">Pos</td>
        <td width="20%">Satellite</td>
        <td width="7%">Frequence</td>
        <td width="2%">Pol</td>
        <td class="w3-hide-small" width="3%">Txp</td>
        <td class="w3-hide-small" width="10%">Beam</td>
        <td width="8%">Standard</td>
        <td width="8%">Modulation</td>
        <td width="8%">SR/FEC</td>
        <td width="20%" class="w3-hide-small">Network, bitrate</td>
        <td class="w3-hide-small" width="4%">NID</td>
        <td class="w3-hide-small" width="4%">TID</td>
        <td dir="ltr" width="2%" align="right"><a href="contribution.php?f="><img src="/edit.gif" alt="" border=0></a></td>
    </tr></table>
    """


class TestValidateHeaderTable(unittest.TestCase):
    @classmethod
    def load_html_content(cls):
        """
        Helper function to load HTML content from a file in the resources directory for testing.
        """
        # Get the absolute path of the current test file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Construct the path to the resources directory
        resources_dir = os.path.join(current_dir, "resources")
        # Construct the full path to the HTML file
        html_file_path = os.path.join(
            resources_dir, "Astra 2E _ Astra 2F _ Astra 2G (28.2°E) - All transmissions - frequencies - KingOfSat.html"
        )

        if not os.path.exists(html_file_path):
            raise FileNotFoundError(f"Test HTML file not found: {html_file_path}")

        with open(html_file_path, "r", encoding="utf-8") as file:
            return file.read()

    def test_valid_header_table(self):
        """
        Test if the header table is correctly validated with valid headers.
        """
        html_content = self.load_html_content()
        scraper = KingOfSatScraper(html_content)

        # Validating the header table
        is_valid = scraper._validate_header_table()

        self.assertTrue(is_valid, "The header table should be valid with the correct column names.")

    def test_missing_column_in_header(self):
        """
        Test the edge case where a column is missing from the header table.
        """
        html_content = self.load_html_content()

        # Simulate missing column (removing one column from the header)
        html_content_invalid = html_content.replace('<td class="pos" dir="ltr">Pos</td>', "")
        scraper = KingOfSatScraper(html_content_invalid)

        # Validating the header table
        is_valid = scraper._validate_header_table()

        self.assertFalse(is_valid, "The header table should be invalid if a column is missing.")

    def test_extra_column_in_header(self):
        """
        Test the edge case where an extra column is added to the header table.
        """
        html_content = self.load_html_content()

        # Simulate extra column (adding a column to the header)
        html_content_invalid = html_content.replace("</tr></table>", "<td>Extra Column</td></tr></table>")
        scraper = KingOfSatScraper(html_content_invalid)

        # Validating the header table
        is_valid = scraper._validate_header_table()

        self.assertFalse(is_valid, "The header table should be invalid if there is an extra column.")

    def test_invalid_header_order(self):
        """
        Test the edge case where the column order in the header is incorrect.
        """
        html_content = self.load_html_content()

        # Simulate incorrect column order (e.g., switch 'Pos' and 'Satellite')
        html_content_invalid = html_content.replace(
            '<td class="pos" dir="ltr">Pos</td>', '<td class="pos" dir="ltr">Satellite</td>'
        ).replace('<td width="20%">Satellite</td>', '<td width="20%">Pos</td>')
        scraper = KingOfSatScraper(html_content_invalid)

        # Validating the header table
        is_valid = scraper._validate_header_table()

        self.assertFalse(is_valid, "The header table should be invalid if the column order is incorrect.")

    def test_valid_header_with_edge_case_content(self):
        """
        Test if the header table can be validated when there are special characters or unusual content.
        """
        html_content = self.load_html_content()

        # Simulate a special character or strange formatting in the header
        html_content_invalid = html_content.replace("Pos", "Pos ☃️")
        scraper = KingOfSatScraper(html_content_invalid)

        # Validating the header table
        is_valid = scraper._validate_header_table()

        self.assertFalse(is_valid, "The header table should be invalid if the column name contains special characters.")


class TestParseTransponders(unittest.TestCase):
    @classmethod
    def load_html_content(cls):
        """
        Helper function to load HTML content from a file in the resources directory for testing.
        """
        # Get the absolute path of the current test file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Construct the path to the resources directory
        resources_dir = os.path.join(current_dir, "resources")
        # Construct the full path to the HTML file
        html_file_path = os.path.join(
            resources_dir, "Astra 2E _ Astra 2F _ Astra 2G (28.2°E) - All transmissions - frequencies - KingOfSat.html"
        )

        if not os.path.exists(html_file_path):
            raise FileNotFoundError(f"Test HTML file not found: {html_file_path}")

        with open(html_file_path, "r", encoding="utf-8") as file:
            return file.read()

    def test_parse_transponders(self):
        """
        Test the parse_transponders method with the actual HTML file.
        """
        html_content = self.load_html_content()
        scraper = KingOfSatScraper(html_content)
        transponders = scraper.parse_transponders()

        # Validate that transponders are parsed correctly
        self.assertGreater(len(transponders), 0, "No transponders were parsed.")

        assert len(transponders) == 39

        first_transponder = transponders[0]
        assert first_transponder.position == "28.2°E"
        assert first_transponder.satellite == "Astra 2E"
        assert first_transponder.frequency == 10773.0
        assert first_transponder.polarization == "H"
        assert first_transponder.transponder_id == 45
        assert first_transponder.beam == "U.K."
        assert first_transponder.system == "DVB-S2"
        assert first_transponder.modulation == "8PSK"
        assert first_transponder.symbol_rate == 23000.0
        assert first_transponder.fec == "3/4"
        assert first_transponder.network_bitrate == "50.1 Mb/s"
        assert first_transponder.nid == 2
        assert first_transponder.tid == 2045

        last_transponder = transponders[-1]
        assert last_transponder.position == "28.2°E"
        assert last_transponder.satellite == "Astra 2E"
        assert last_transponder.frequency == 12382.00
        assert last_transponder.polarization == "H"
        assert last_transponder.transponder_id == 35
        assert last_transponder.beam == "U.K."
        assert last_transponder.system == "DVB-S2"
        assert last_transponder.modulation == "8PSK"
        assert last_transponder.symbol_rate == 29500.0
        assert last_transponder.fec == "2/3"
        assert last_transponder.network_bitrate == "ASTRA, 57.1 Mb/s"
        assert last_transponder.nid == 2
        assert last_transponder.tid == 2035

    def test_parse_transponder_with_missing_fields(self):
        """
        Test parser behavior when a transponder row is missing fields.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><a>Astra 2E</a></td>
            <td>11097.00</td>
            <td>V</td>
            <!-- Missing transponder ID and other columns -->
        </tr></table>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip malformed row with missing fields.")

    def test_parse_transponder_with_non_numeric_frequency(self):
        """
        Ensure parser handles non-numeric frequency gracefully.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><a>Astra 2E</a></td>
            <td>InvalidFreq</td>
            <td>H</td>
            <td><a>45</a></td>
            <td><a>U.K.</a></td>
            <td>DVB-S2</td>
            <td>8PSK</td>
            <td><a>23000</a> <a>3/4</a></td>
            <td>50.1 Mb/s</td>
            <td>2</td>
            <td>2045</td>
        </tr></table>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip rows with non-numeric frequency.")

    def test_parse_transponder_with_nested_elements(self):
        """
        Test that parser handles nested tags inside td elements correctly.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><span class="nbc">9</span><a>Astra 2E</a></td>
            <td>10773.00</td>
            <td>H</td>
            <td><a>45</a></td>
            <td><a>U.K.</a></td>
            <td>DVB-S2</td>
            <td>8PSK</td>
            <td><a>23000</a> <a>3/4</a></td>
            <td>50.1 Mb/s</td>
            <td>2</td>
            <td>2045</td>
        </tr></table>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        self.assertEqual(transponders[0].satellite, "Astra 2E")

    def test_parse_transponder_with_weird_symbol_rate_format(self):
        """
        Symbol rate and FEC not inside <a> tags.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><a>Astra 2F</a></td>
            <td>11097.00</td>
            <td>V</td>
            <td><a>99</a></td>
            <td><a>U.K.</a></td>
            <td>DVB-S2</td>
            <td>QPSK</td>
            <td>27500 5/6</td>
            <td>55.0 Mb/s</td>
            <td>1</td>
            <td>2001</td>
        </tr></table>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        # Since <a> not used, fallback
        self.assertEqual(transponders[0].symbol_rate, 0.0)

    def test_parse_transponder_with_extra_columns(self):
        """
        Transponder row has more columns than expected.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><a>Astra 2G</a></td>
            <td>11223.00</td>
            <td>H</td>
            <td><a>47</a></td>
            <td><a>Europe</a></td>
            <td>DVB-S2</td>
            <td>8PSK</td>
            <td><a>27500</a> <a>2/3</a></td>
            <td>60.5 Mb/s</td>
            <td>2</td>
            <td>2047</td>
            <td>Extra</td>
            <td>MoreExtra</td>
        </tr></table>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        self.assertEqual(transponders[0].transponder_id, 47)

    def test_parse_transponder_with_corrupt_html(self):
        """
        Handle broken HTML structure gracefully.
        """
        html_snippet = (
            VALID_HEADER_HTML
            + """
        <table class="frq"><tr>
            <td class="pos">28.2°E<td>
            <td><a>Astra 2E</td>
            <td>10773.00
            <td>H</td><td><a>45</a>
        """
        )
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip corrupt HTML rows without crashing.")


class TestParseApids(unittest.TestCase):
    def setUp(self):
        self.scraper = KingOfSatScraper("<html></html>")  # Dummy init

    def make_apid_cell(self, html: str):
        """Helper to create a BeautifulSoup tag from HTML string."""
        return BeautifulSoup(html, "html.parser")

    def test_single_apid_with_lang(self):
        html = '6002<a title="English"> <font color="blue">eng</font></a>'
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(apids, {"eng": 6002})

    def test_apid_with_nar_tag_should_be_last(self):
        html = '5101<a title="AC3"><img></a><a title="English"> <font color="blue">eng</font></a><br>5105 nar'
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(list(apids.keys())[-1], "nar")
        self.assertEqual(apids, {"eng": 5101, "nar": 5105})

    def test_apids_with_multiple_langs(self):
        html = '2342<a title="English"> <font color="blue">eng</font></a><br>2341<a title="NAR"> <font color="blue">nar</font></a>'
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(apids, {"eng": 2342, "nar": 2341})

    def test_apids_without_language_tags(self):
        html = "6002<br>6003"
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(len(apids), 0)  # No <font> means no lang info

    def test_empty_apid_cell(self):
        html = "&nbsp;"
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(apids, {})

    def test_unstructured_but_valid_apids(self):
        html = '5105 nar<br>5101<a title="English"> <font color="blue">eng</font></a>'
        cell = self.make_apid_cell(html)
        apids = self.scraper.parse_apids(cell)
        self.assertEqual(apids, {"eng": 5101, "nar": 5105})


class TestParseChannels(unittest.TestCase):
    @classmethod
    def load_html_content(cls):
        """
        Helper function to load HTML content from a file in the resources directory for testing.
        """
        # Get the absolute path of the current test file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Construct the path to the resources directory
        resources_dir = os.path.join(current_dir, "resources")
        # Construct the full path to the HTML file
        html_file_path = os.path.join(
            resources_dir, "Astra 2E _ Astra 2F _ Astra 2G (28.2°E) - All transmissions - frequencies - KingOfSat.html"
        )

        if not os.path.exists(html_file_path):
            raise FileNotFoundError(f"Test HTML file not found: {html_file_path}")

        with open(html_file_path, "r", encoding="utf-8") as file:
            return file.read()

    def test_parse_channels(self):
        """
        Test the parse_channels method with the actual HTML file.
        """
        html_content = self.load_html_content()
        scraper = KingOfSatScraper(html_content)
        channels = scraper.parse_channels()

        # Validate that channels are parsed correctly
        self.assertGreater(len(channels), 0, "No channels were parsed.")

        assert len(channels) == 292

        first_channel = channels[0]
        assert first_channel.channel_type == "v"
        assert first_channel.name == "BBC Parliament HD"
        assert first_channel.country == "United Kingdom"
        assert first_channel.category == "Politics"
        assert first_channel.packages == ["Sky Digital"]
        assert first_channel.encryption == "Clear"
        assert first_channel.sid == 6308
        assert first_channel.vpid == 5400
        assert first_channel.apids == {"eng": 5401}
        assert first_channel.pmt == 256
        assert first_channel.pcr == 5400
        assert first_channel.txt == 5403
        assert first_channel.last_updated == datetime(year=2023, month=2, day=21)

        feed_channel = channels[4]
        assert feed_channel.channel_type == "feed"
        assert feed_channel.name == "BBC Two NI HD"
        assert feed_channel.country is None
        assert feed_channel.category is None
        assert feed_channel.packages == ["Sky Digital"]
        assert feed_channel.encryption == "Clear"
        assert feed_channel.sid == 6332
        assert feed_channel.vpid == 5300
        assert feed_channel.apids == {"eng": 5301, "nar": 5305}
        assert feed_channel.pmt == 263
        assert feed_channel.pcr == 5300
        assert feed_channel.txt == 5303
        assert feed_channel.last_updated == datetime(year=2023, month=3, day=14)

        radio_channel = channels[6]
        assert radio_channel.channel_type == "r"
        assert radio_channel.name == "BBC R5SX"
        assert radio_channel.country is None
        assert radio_channel.category is None
        assert radio_channel.packages == ["Sky Digital"]
        assert radio_channel.encryption == "Clear"
        assert radio_channel.sid == 6339
        assert radio_channel.vpid is None
        assert radio_channel.apids == {"eng": 6011}
        assert radio_channel.pmt == 309
        assert radio_channel.pcr == 6011
        assert radio_channel.txt is None
        assert radio_channel.last_updated == datetime(year=2024, month=1, day=10)

        last_channel = channels[-1]
        assert last_channel.channel_type == "v"
        assert last_channel.name == "Quest +1"
        assert last_channel.country == "United Kingdom"
        assert last_channel.category == "Entertainment"
        assert last_channel.packages == ["Sky Digital"]
        assert last_channel.encryption == "Clear"
        assert last_channel.sid == 6215
        assert last_channel.vpid == 2360
        assert last_channel.apids == {"eng": 2361, "nar": 2362}
        assert last_channel.pmt == 264
        assert last_channel.pcr == 2360
        assert last_channel.txt == 2363
        assert last_channel.last_updated == datetime(year=2024, month=7, day=10)

    def test_parse_channel_with_missing_optional_fields(self):
        html = """
        <table class="fl">
            <tr bgcolor="white">
                <td class="v px3"></td>
                <td><img src="/zap.gif"></td>
                <td class="ch">Test Channel</td>
                <td class="w3-hide-small pays"></td>
                <td class="w3-hide-small genre"></td>
                <td class="w3-hide-small bq"><a class="bq" href="#">Sky Digital</a></td>
                <td class="cr">Clear</td>
                <td class="s">1234</td>
                <td class="w3-hide-small mpeg4">5000</td>
                <td>5001<a title="English"><font color="blue">eng</font></a></td>
                <td class="w3-hide-small pid">250</td>
                <td class="w3-hide-small pid">5000</td>
                <td class="w3-hide-small pid"></td>
                <td class="maj"><a>2024-03-01</a></td>
            </tr>
        </table>
        """
        scraper = KingOfSatScraper(html)
        channels = scraper.parse_channels()
        self.assertEqual(len(channels), 1)
        channel = channels[0]
        self.assertEqual(channel.name, "Test Channel")
        self.assertIsNone(channel.country)
        self.assertIsNone(channel.category)
        self.assertEqual(channel.txt, None)

    def test_parse_channel_with_multiple_packages(self):
        html = """
        <table class="fl">
            <tr bgcolor="white">
                <td class="v px3"></td>
                <td><img src="/zap.gif"></td>
                <td class="ch">Multi Package TV</td>
                <td class="w3-hide-small pays">UK</td>
                <td class="w3-hide-small genre">Entertainment</td>
                <td class="w3-hide-small bq">
                    <a class="bq" href="#">Sky Digital</a>,
                    <a class="bq" href="#">Freesat</a>
                </td>
                <td class="cr">Clear</td>
                <td class="s">5678</td>
                <td class="w3-hide-small mpeg4">5000</td>
                <td>5001<a title="English"><font color="blue">eng</font></a></td>
                <td class="w3-hide-small pid">251</td>
                <td class="w3-hide-small pid">5000</td>
                <td class="w3-hide-small pid">5002</td>
                <td class="maj"><a>2024-03-10</a></td>
            </tr>
        </table>
        """
        scraper = KingOfSatScraper(html)
        channels = scraper.parse_channels()
        self.assertEqual(channels[0].packages, ["Sky Digital", "Freesat"])

    def test_parse_radio_channel(self):
        html = """
        <table class="fl">
            <tr bgcolor="white">
                <td class="r px3"></td>
                <td><img src="/radio.gif"></td>
                <td class="ch">Radio Test</td>
                <td class="w3-hide-small pays"></td>
                <td class="w3-hide-small genre"></td>
                <td class="w3-hide-small bq"><a class="bq" href="#">Sky Digital</a></td>
                <td class="cr">Clear</td>
                <td class="s">7890</td>
                <td class="w3-hide-small"></td>
                <td>6002<a title="English"><font color="blue">eng</font></a></td>
                <td class="w3-hide-small pid">260</td>
                <td class="w3-hide-small pid">6002</td>
                <td class="w3-hide-small pid"></td>
                <td class="maj"><a>2024-01-01</a></td>
            </tr>
        </table>
        """
        scraper = KingOfSatScraper(html)
        channel = scraper.parse_channels()[0]
        self.assertEqual(channel.name, "Radio Test")
        self.assertEqual(channel.vpid, None)
        self.assertEqual(channel.apids, {"eng": 6002})

    def test_parse_channel_with_invalid_date(self):
        html = """
        <table class="fl">
            <tr bgcolor="white">
                <td class="v px3"></td>
                <td><img src="/zap.gif"></td>
                <td class="ch">Date Fail TV</td>
                <td class="w3-hide-small pays">UK</td>
                <td class="w3-hide-small genre">News</td>
                <td class="w3-hide-small bq"><a class="bq" href="#">Sky Digital</a></td>
                <td class="cr">Clear</td>
                <td class="s">5555</td>
                <td class="w3-hide-small mpeg4">4000</td>
                <td>4001<a title="English"><font color="blue">eng</font></a></td>
                <td class="w3-hide-small pid">240</td>
                <td class="w3-hide-small pid">4000</td>
                <td class="w3-hide-small pid">4002</td>
                <td class="maj"><a>bad-date</a></td>
            </tr>
        </table>
        """
        scraper = KingOfSatScraper(html)
        with self.assertLogs(level="WARNING") as log:
            channels = scraper.parse_channels()
            self.assertEqual(len(channels), 0)
            self.assertTrue(any("Error parsing channel row" in message for message in log.output))


if __name__ == "__main__":
    unittest.main()
