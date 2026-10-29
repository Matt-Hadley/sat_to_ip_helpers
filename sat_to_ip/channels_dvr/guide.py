"""Gracenote guide data, as Channels DVR serves it: match channels to call signs and station IDs."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from sat_to_ip.channels_dvr.client import ChannelsDVRClient
from sat_to_ip.config import Section
from sat_to_ip.mappings import available_regions, load_mappings
from sat_to_ip.model import Channel, GuideMatch, GuideRef, Lineup
from sat_to_ip.names import normalize_name

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8089"
DEFAULT_STATION_LIST = "GBR-1000193-DEFAULT"


@dataclass
class GracenoteGuide:
    """Matches by explicit Name→call sign mapping first, then by normalised Gracenote name or call sign."""

    name: str
    url: str
    sid: str | None
    station_list: str
    region: str | None
    mappings_file: Path | None
    kind: str = "gracenote"

    @cached_property
    def client(self) -> ChannelsDVRClient:
        return ChannelsDVRClient.connect(self.url, self.sid)

    def match(self, lineup: Lineup) -> GuideMatch:
        stations, by_name = _lookups(self.client.get_gracenote(self.station_list))
        explicit = load_mappings("gracenote", self.region, self.mappings_file)
        refs: dict[str, GuideRef] = {}
        unmatched: list[Channel] = []
        for ch in lineup:
            # An explicit mapping wins even when its call sign is missing from the list (then: unmatched)
            callsign = explicit.get(ch.name.lower()) or by_name.get(normalize_name(ch.name))
            if callsign is None or callsign not in stations:
                logger.debug(f"No Gracenote match for: {ch.name!r}")
                unmatched.append(ch)
                continue
            refs[ch.key] = stations[callsign]
        return GuideMatch(self.name, self.kind, refs, tuple(unmatched))


def _lookups(data: list[dict[str, Any]]) -> tuple[dict[str, GuideRef], Mapping[str, str]]:
    """call sign → GuideRef, and normalised name / call sign → call sign. Later entries win."""
    stations: dict[str, GuideRef] = {}
    by_name: dict[str, str] = {}
    for entry in data:
        callsign, name, station_id = (str(entry.get(k) or "").strip() for k in ("callSign", "name", "stationId"))
        if not callsign:
            continue
        stations[callsign] = GuideRef(guide_id=callsign, display_name=name or None, station_id=station_id)
        if name:
            by_name[normalize_name(name)] = callsign
        by_name[normalize_name(callsign)] = callsign
    return stations, by_name


def from_config(section: Section) -> GracenoteGuide:
    region = section.get_str("mappings", "uk") or None
    if region and region.lower() not in available_regions("gracenote"):
        raise section.error("mappings", f"no built-in region {region!r} (have: {available_regions('gracenote')})")
    mappings_file = section.get_optional_path("mappings_file")
    if mappings_file and not mappings_file.is_file():
        raise section.error("mappings_file", f"no such file {mappings_file}")
    return GracenoteGuide(
        name=section.name,
        url=section.get_url("url", DEFAULT_URL),
        sid=section.get_secret("sid_env", required=False),
        station_list=section.get_str("station_list", DEFAULT_STATION_LIST),
        region=region,
        mappings_file=mappings_file,
    )
