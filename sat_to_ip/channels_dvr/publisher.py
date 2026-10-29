"""Publish the lineup to Channels DVR by replacing the text of its custom M3U sources (one TV, one radio)."""

import logging
from dataclasses import dataclass, field
from functools import cached_property

from sat_to_ip import m3u
from sat_to_ip.channels_dvr.client import ChannelsDVRClient
from sat_to_ip.checks import publishable
from sat_to_ip.config import Section
from sat_to_ip.model import Channel, GuideMatch, Kind, Lineup, PublishResult

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8089"
CHANNEL_NUMBERS = ("channels_dvr", "list")


@dataclass(frozen=True)
class Source:
    """A Channels DVR custom M3U source that receives the channels of one kind."""

    kind: Kind
    source_id: str
    display_name: str | None = None  # None leaves the name the user gave it in Channels DVR


@dataclass
class ChannelsDVRPublisher:
    name: str
    url: str
    sid: str | None
    guide: str | None
    sources: tuple[Source, ...]
    channel_numbers: str = "channels_dvr"
    accepts_guides: frozenset[str] = field(default=frozenset({"gracenote"}))

    @cached_property
    def client(self) -> ChannelsDVRClient:
        return ChannelsDVRClient.connect(self.url, self.sid)

    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult:
        # Build every playlist before pushing any, so a refusal leaves all sources untouched
        playlists: list[tuple[Source, list[m3u.Entry]]] = []
        for src in self.sources:
            channels = lineup.of_kind(src.kind)
            if not channels:
                logger.info(f"No {src.kind} channels in the lineup; leaving {src.source_id} unchanged")
                continue
            numbered = self.channel_numbers == "list"
            chosen = publishable(lineup, guide, src.kind, f"Channels DVR source {src.source_id!r}")
            playlists.append((src, [_entry(ch, guide, numbered) for ch in chosen]))

        # Read every source before writing any: a missing one fails the run with nothing changed
        current = [self.client.get_m3u_source(src.source_id) for src, _ in playlists]
        for (src, entries), settings in zip(playlists, current, strict=True):
            if dry_run:
                logger.info(f"Dry run: would push {len(entries)} {src.kind} channels to {src.source_id}")
                continue
            # Put back what the user configured in Channels; only the playlist (and what we own) changes
            settings = {**settings, "text": m3u.render(entries)}
            if src.display_name:
                settings["name"] = src.display_name
            if self.channel_numbers == "list":
                settings["numbering"] = ""  # "Prefer channel-number from M3U"
            self.client.update_m3u_source(src.source_id, settings)
            self.client.refresh_m3u_source(src.source_id)
        detail = "; ".join(f"{src.kind}: {len(entries)} → {src.source_id}" for src, entries in playlists)
        suffix = " (dry run; sources found)" if dry_run else ""
        return PublishResult(self.name, sum(len(e) for _, e in playlists), detail + suffix)


def _entry(ch: Channel, guide: GuideMatch | None, numbered: bool) -> m3u.Entry:
    attrs: tuple[tuple[str, str], ...] = ()
    if guide is not None:
        ref = guide.refs[ch.key]
        attrs = (
            ("channel-id", ref.guide_id),
            ("tvc-guide-stationid", ref.station_id or ""),
            ("tvg-name", ref.display_name or ch.name),
        )
    if numbered:
        attrs += (("channel-number", str(ch.number)),)
    return m3u.Entry(ch.name, ch.stream_url, attrs)


def from_config(section: Section) -> ChannelsDVRPublisher:
    sources = []
    kinds: tuple[Kind, ...] = ("tv", "radio")
    for kind in kinds:
        source_id = section.get_optional_str(f"{kind}_source")
        display_name = section.get_optional_str(f"{kind}_display_name")
        if source_id:
            sources.append(Source(kind, source_id, display_name))
        elif display_name:
            raise section.error(f"{kind}_display_name", f"set {kind}_source too, or remove this")
    if not sources:
        raise section.error("tv_source", "set tv_source and/or radio_source (the Channels DVR M3U source IDs)")
    channel_numbers = section.get_str("channel_numbers", "channels_dvr")
    if channel_numbers not in CHANNEL_NUMBERS:
        raise section.error("channel_numbers", f"must be one of {', '.join(CHANNEL_NUMBERS)}, got {channel_numbers!r}")
    return ChannelsDVRPublisher(
        name=section.name,
        url=section.get_url("url", DEFAULT_URL),
        sid=section.get_secret("sid_env", required=False),
        guide=section.get_optional_str("guide"),
        sources=tuple(sources),
        channel_numbers=channel_numbers,
    )
