"""Name→GuideId tables: built-in per guide kind and region, plus the config's extra_mappings.

Built-ins live in sat_to_ip/data/mappings/<guide kind>/<region>/*.csv with the header Name,GuideId.
"""

import csv
import logging
from collections.abc import Mapping
from pathlib import Path

from sat_to_ip.exceptions import ConfigError

BUILTIN_DIR = Path(__file__).parent / "data" / "mappings"

logger = logging.getLogger(__name__)


def available_regions(kind: str) -> list[str]:
    kind_dir = BUILTIN_DIR / kind
    return sorted(p.name for p in kind_dir.iterdir() if p.is_dir()) if kind_dir.is_dir() else []


def load_mappings(kind: str, region: str | None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return lowercased channel name → guide ID. `extra` (the config's extra_mappings) wins over built-ins."""
    mappings: dict[str, str] = {}
    if region:
        region_dir = BUILTIN_DIR / kind / region.lower()
        if not region_dir.is_dir():
            raise ConfigError(
                f"No built-in {kind} mappings for region {region!r}. Available: {available_regions(kind)}"
            )
        for path in sorted(region_dir.glob("*.csv")):
            mappings.update(load_csv(path))
    mappings.update({name.lower(): guide_id for name, guide_id in (extra or {}).items()})
    logger.debug(f"Loaded {len(mappings)} {kind} mappings (region={region!r}, {len(extra or {})} extra)")
    return mappings


def load_csv(path: Path) -> dict[str, str]:
    """Read a Name,GuideId CSV. Blank rows are skipped; a bad header is a ConfigError."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or not {"Name", "GuideId"} <= set(reader.fieldnames):
                raise ConfigError(f"{path}: expected a header row 'Name,GuideId', got {reader.fieldnames}")
            return {
                row["Name"].strip().lower(): row["GuideId"].strip()
                for row in reader
                if (row.get("Name") or "").strip() and (row.get("GuideId") or "").strip()
            }
    except OSError as exc:
        raise ConfigError(f"Can't read mappings file {path}: {exc}") from exc
