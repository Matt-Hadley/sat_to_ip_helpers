"""Pure functions for converting KingOfSat Transponder objects into the JSON
payload expected by Octopus's /channelsearch/uploadCustom endpoint."""

from king_of_sat_scraper.transponder import Transponder


def format_source(transponders: list[Transponder], position: str, satellite: str) -> dict:
    """Build one Octopus SourceList entry from a list of Transponders.

    Args:
        transponders: Parsed transponders for this satellite position.
        position:     Satellite position string, e.g. '28.2E'.
        satellite:    Satellite name, e.g. 'Astra 2E'.

    Returns:
        A dict with Title, DVBType, Key, and TransponderList.
    """
    key = position.replace(".", "").replace("°", "")
    transponder_list = [
        {
            "Request": (
                f"freq={int(float(t.frequency))}"
                f"&pol={t.polarization.lower()}"
                f"&mtype={t.modulation}"
                f"&msys={t.system.lower().replace('-', '')}"
                f"&sr={int(float(t.symbol_rate))}"
            )
        }
        for t in transponders
    ]
    return {
        "Title": f"{position} - {satellite}",
        "DVBType": "S",  # "S" = satellite (covers both DVB-S and DVB-S2)
        "Key": key,
        "TransponderList": transponder_list,
    }


def build_upload_payload(sources: list[dict]) -> dict:
    """Wrap a list of source dicts into the top-level uploadCustom payload."""
    return {"GroupList": [], "SourceList": sources}
