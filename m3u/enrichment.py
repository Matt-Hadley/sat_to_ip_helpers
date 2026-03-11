"""Pure M3U enrichment logic — no file I/O.

These functions operate entirely on in-memory data so they are easy to unit
test.  File loading and CLI wiring live in m3u/scripts/enrich_m3u.py.
"""

import logging
import re
from typing import NamedTuple

logger = logging.getLogger(__name__)


class EnrichmentResult(NamedTuple):
    text: str
    enriched: int
    skipped: int
    skipped_channels: list[str]


class GracenoteLookups(NamedTuple):
    callsign_to_info: dict[str, dict[str, str]]
    name_to_callsign: dict[str, str]


def normalize_name(name: str) -> str:
    """Lowercase and strip all non-alphanumeric characters for fuzzy matching."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_gracenote_lookups(data: list[dict]) -> GracenoteLookups:
    """Build lookup maps from a raw Gracenote station list.

    Args:
        data: List of Gracenote station dicts (as returned by Channels DVR's
              /dvr/guide/stations/<name> endpoint).

    Returns:
        GracenoteLookups containing:
            callsign_to_info:  callsign → {channel, stationId, name}
            name_to_callsign:  normalized name/callsign → callsign
    """
    callsign_to_info: dict[str, dict[str, str]] = {}
    name_to_callsign: dict[str, str] = {}
    for entry in data:
        callsign = entry.get("callSign", "").strip()
        name = entry.get("name", "").strip()
        station_id = entry.get("stationId", "").strip()
        channel_num = entry.get("channel", "").strip()
        if not callsign:
            continue
        callsign_to_info[callsign] = {
            "channel": channel_num,
            "stationId": station_id,
            "name": name,
        }
        if name:
            name_to_callsign[normalize_name(name)] = callsign
        name_to_callsign[normalize_name(callsign)] = callsign
    return GracenoteLookups(callsign_to_info=callsign_to_info, name_to_callsign=name_to_callsign)


def enrich_m3u_text(
    m3u_text: str,
    explicit_mappings: dict[str, str],
    lookups: GracenoteLookups,
) -> EnrichmentResult:
    """Enrich an M3U string with Gracenote channel-id and guide station ID.

    Matching strategy (in order):
      1. Explicit override from a Name→Callsign mapping dict.
      2. Fuzzy match on the normalized channel name / callsign against
         Gracenote entries.

    The Octopus M3U uses the callsign as the label after the comma
    (e.g. '#EXTINF:-1 ...,BBC1NWHD'), so tvg-name is populated from the
    Gracenote display name when available.

    Args:
        m3u_text:          Raw M3U content.
        explicit_mappings: channel-label → callsign overrides (may be empty).
        callsign_to_info:  From build_gracenote_lookups().
        name_to_callsign:  From build_gracenote_lookups().

    Returns:
        EnrichmentResult(text, enriched_count, skipped_count)
    """
    lines = m3u_text.splitlines()
    output = ["#EXTM3U"]
    enriched = skipped = 0
    skipped_channels: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            channel_label = line.split(",", 1)[1].strip()
            stream_url = lines[i + 1].strip() if i + 1 < len(lines) else ""

            callsign = explicit_mappings.get(channel_label)
            if not callsign:
                callsign = lookups.name_to_callsign.get(normalize_name(channel_label))

            if callsign is None or callsign not in lookups.callsign_to_info:
                logger.debug(f"No Gracenote match for: {channel_label!r}")
                skipped_channels.append(channel_label)
                skipped += 1
                i += 2
                continue

            # callsign is now guaranteed to be a str and present in callsign_to_info
            info = lookups.callsign_to_info[callsign]
            display_name = info["name"] or channel_label
            output.append(
                f'#EXTINF:-1 channel-id="{callsign}" '
                f'tvc-guide-stationid="{info["stationId"]}" '
                f'tvg-name="{display_name}",{channel_label}'
            )
            output.append(stream_url)
            enriched += 1
            i += 2
        else:
            i += 1

    return EnrichmentResult(
        text="\n".join(output) + "\n",
        enriched=enriched,
        skipped=skipped,
        skipped_channels=skipped_channels,
    )
