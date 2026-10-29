"""Pure M3U enrichment logic — no file I/O.

These functions operate entirely on in-memory data so they are easy to unit
test.  File loading and CLI wiring live in steps.py and cli.py.
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


def parse_m3u(m3u_text: str) -> list[tuple[str, str, str]]:
    """Return (extinf_line, label, stream_url) for each entry; the URL is the line after #EXTINF."""
    lines = m3u_text.splitlines()
    entries = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            url = lines[i + 1].strip() if i + 1 < len(lines) else ""
            entries.append((line, line.partition(",")[2].strip(), url))
    return entries


def split_m3u(m3u_text: str, dms_channels: list[dict]) -> tuple[str, str]:
    """Split an M3U into TV and radio playlists, in dms_channels order.

    Octopus M3U labels match DMS channel names.  Channels that share a name
    take the M3U entries with that name in order, so each keeps its own URL.
    """
    by_label: dict[str, list[tuple[str, str]]] = {}
    for extinf, label, url in parse_m3u(m3u_text):
        by_label.setdefault(label, []).append((extinf, url))

    tv_lines = ["#EXTM3U"]
    radio_lines = ["#EXTM3U"]
    for ch in dms_channels:
        matches = by_label.get(ch.get("name", ""))
        if not matches:
            continue
        target = radio_lines if ch.get("type") == "audio" else tv_lines
        target.extend(matches.pop(0))

    return "\n".join(tv_lines) + "\n", "\n".join(radio_lines) + "\n"


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
    explicit_lower = {k.lower(): v for k, v in explicit_mappings.items()}
    output = ["#EXTM3U"]
    enriched = skipped = 0
    skipped_channels: list[str] = []
    for _, channel_label, stream_url in parse_m3u(m3u_text):
        callsign = explicit_lower.get(channel_label.lower())
        if not callsign:
            callsign = lookups.name_to_callsign.get(normalize_name(channel_label))

        if callsign is None or callsign not in lookups.callsign_to_info:
            logger.debug(f"No Gracenote match for: {channel_label!r}")
            skipped_channels.append(channel_label)
            skipped += 1
            continue

        info = lookups.callsign_to_info[callsign]
        display_name = info["name"] or channel_label
        output.append(
            f'#EXTINF:-1 channel-id="{callsign}" '
            f'tvc-guide-stationid="{info["stationId"]}" '
            f'tvg-name="{display_name}",{channel_label}'
        )
        output.append(stream_url)
        enriched += 1

    return EnrichmentResult(
        text="\n".join(output) + "\n",
        enriched=enriched,
        skipped=skipped,
        skipped_channels=skipped_channels,
    )
