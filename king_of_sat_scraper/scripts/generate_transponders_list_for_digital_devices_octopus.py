#!/usr/bin/env python3

import argparse
import json
import logging
import os

import requests

from king_of_sat_scraper.scraper import KingOfSatScraper

# Default BASE_URL
DEFAULT_BASE_URL = "https://en.kingofsat.net/freqs.php"

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)


def build_url(base_url, position, filtre, cl):
    url = f"{base_url}?pos={position}&standard=All&ordre=freq&filtre={filtre}&cl={cl}"
    logging.debug(f"Built URL: {url}")
    return url


def fetch_html_content(url):
    """
    Fetch the HTML content from the URL.
    :param url: The URL to fetch
    :return: HTML content as a string
    """
    logging.info(f"📡 Fetching data from: {url}")
    try:
        r = requests.get(url)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        logging.error(f"❌ Failed to fetch data from {url}: {e}")
        return None


def parse_kingofsat(base_url, position, filtre, cl):
    url = build_url(base_url, position, filtre, cl)

    html_content = fetch_html_content(url)
    if html_content is None:
        return {}

    # Instantiate KingOfSatScraper and parse transponders
    scraper = KingOfSatScraper(html_content)
    transponders = scraper.parse_transponders()

    if not transponders:
        logging.warning("⚠️ No transponder data found.")

    # Create the formatted result
    satellite_name = transponders[0].satellite if transponders else "Unknown Satellite"
    title = f"{position} - {satellite_name}"
    # You can adjust this logic as needed
    key = position.replace(".", "").replace("°", "")

    transponder_list = [
        {
            "Request": f"freq={transponder.frequency}&pol={transponder.polarization.lower()}&mtype={transponder.modulation}&msys={transponder.system.lower().replace('-', '')}&sr={transponder.symbol_rate}"
        }
        for transponder in transponders
    ]

    # Structure the output as required
    result = {
        "Title": title,
        "DVBType": "S",  # Assuming DVB-S, adjust if needed
        "Key": key,
        "TransponderList": transponder_list,
    }

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape KingOfSat FTA or encrypted transponders")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="Base URL for KingOfSat")
    parser.add_argument("--position", type=str, default="28.2E", help="Satellite position (e.g. 28.2E, 13.0E)")
    parser.add_argument("--filtre", type=str, default="Clear", help="Filter: Clear (FTA), All, or Encrypted")
    parser.add_argument("--cl", type=str, default="eng", help="Channel language filter (e.g. eng, fra, ger)")
    parser.add_argument("--output-dir", type=str, default="output", help="Directory to save output JSON")

    args = parser.parse_args()

    logging.info("Starting transponder scraping script.")
    logging.debug(f"Arguments: {args}")

    # Fetch and parse the transponder data
    transponder_data = parse_kingofsat(
        base_url=args.base_url,
        position=args.position,
        filtre=args.filtre,
        cl=args.cl,
    )

    if transponder_data:
        # Save parsed transponder data to JSON file
        os.makedirs(args.output_dir, exist_ok=True)
        output_path = os.path.join(
            args.output_dir,
            f"transponders_{args.position.replace('.', '').replace('°', '')}_{args.filtre.lower()}_{args.cl}.json",
        )
        try:
            with open(output_path, "w") as f:
                json.dump(transponder_data, f, indent=2)
            logging.info(f"✅ Transponder data saved to {output_path}")
        except IOError as e:
            logging.error(f"❌ Failed to save data to {output_path}: {e}")
    else:
        logging.warning("⚠️ No transponder data to save.")
