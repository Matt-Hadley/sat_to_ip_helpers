"""CSV loader for Name→Callsign override mappings."""
import csv
import logging
from pathlib import Path

import m3u

_BUILTIN_MAPPINGS_DIR = Path(m3u.__file__).parent / "resources" / "channel_name_to_callsign_mappings"

logger = logging.getLogger(__name__)


def available_regions() -> list[str]:
    """Return available built-in mapping region names."""
    if not _BUILTIN_MAPPINGS_DIR.is_dir():
        return []
    return sorted(p.name for p in _BUILTIN_MAPPINGS_DIR.iterdir() if p.is_dir())


def load_region_mappings(region: str) -> dict[str, str]:
    """Load built-in Name→Callsign mappings for a region (e.g. 'uk').

    Merges all CSV files found in the region directory.
    """
    preset_dir = _BUILTIN_MAPPINGS_DIR / region.lower()
    if not preset_dir.is_dir():
        available = available_regions()
        raise ValueError(f"Unknown mapping region {region!r}. Available: {available}")
    mappings: dict[str, str] = {}
    for csv_path in sorted(preset_dir.glob("*.csv")):
        mappings.update(load_csv_mappings(str(csv_path)))
    logger.info(f"Loaded {len(mappings)} built-in mappings for region {region!r}")
    return mappings


def load_csv_mappings(path: str) -> dict[str, str]:
    """Load a Name→Callsign CSV override file."""
    mappings: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
            name_idx = headers.index("Name")
            callsign_idx = headers.index("Callsign")
        except (ValueError, StopIteration):
            logger.warning(f"Invalid CSV headers in {path}, expected Name,Callsign")
            return mappings
        for row in reader:
            if len(row) > max(name_idx, callsign_idx):
                mappings[row[name_idx].strip()] = row[callsign_idx].strip()
    logger.info(f"Loaded {len(mappings)} channel mappings from {path}")
    return mappings
