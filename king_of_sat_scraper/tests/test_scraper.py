import logging
import os
import unittest

from king_of_sat_scraper.scraper import KingOfSatScraper

# Suppress unnecessary debug info for unit tests
logging.basicConfig(level=logging.WARNING)

VALID_HEADER_HTML = """
        <table class="frq"><tr bgcolor="#999999">
        <td class="pos" dir="ltr">Pos</td>
        <td width="20%">Satellite</td>
        <td width="7%">Frequency</td>
        <td width="2%">Pol</td>
        <td class="w3-hide-small" width="3%">Txp</td>
        <td class="w3-hide-small" width="10%">Coverage area</td>
        <td width="8%">Standard</td>
        <td width="8%">Modulation</td>
        <td width="8%">SR/FEC</td>
        <td width="20%" class="w3-hide-small">Network, Bitrate</td>
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
        html_snippet = VALID_HEADER_HTML + """
        <table class="frq"><tr>
            <td class="pos">28.2°E</td>
            <td><a>Astra 2E</a></td>
            <td>11097.00</td>
            <td>V</td>
            <!-- Missing transponder ID and other columns -->
        </tr></table>
        """
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip malformed row with missing fields.")

    def test_parse_transponder_with_non_numeric_frequency(self):
        """
        Ensure parser handles non-numeric frequency gracefully.
        """
        html_snippet = VALID_HEADER_HTML + """
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
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip rows with non-numeric frequency.")

    def test_parse_transponder_with_nested_elements(self):
        """
        Test that parser handles nested tags inside td elements correctly.
        """
        html_snippet = VALID_HEADER_HTML + """
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
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        self.assertEqual(transponders[0].satellite, "Astra 2E")

    def test_parse_transponder_with_weird_symbol_rate_format(self):
        """
        Symbol rate and FEC not inside <a> tags.
        """
        html_snippet = VALID_HEADER_HTML + """
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
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        # Since <a> not used, read the plain text
        self.assertEqual(transponders[0].symbol_rate, 27500)
        self.assertEqual(transponders[0].fec, "5/6")

    def test_parse_transponder_with_extra_columns(self):
        """
        Transponder row has more columns than expected.
        """
        html_snippet = VALID_HEADER_HTML + """
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
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 1)
        self.assertEqual(transponders[0].transponder_id, 47)

    def test_parse_transponder_with_corrupt_html(self):
        """
        Handle broken HTML structure gracefully.
        """
        html_snippet = VALID_HEADER_HTML + """
        <table class="frq"><tr>
            <td class="pos">28.2°E<td>
            <td><a>Astra 2E</td>
            <td>10773.00
            <td>H</td><td><a>45</a>
        """
        scraper = KingOfSatScraper(html_snippet)
        transponders = scraper.parse_transponders()
        self.assertEqual(len(transponders), 0, "Should skip corrupt HTML rows without crashing.")


if __name__ == "__main__":
    unittest.main()
