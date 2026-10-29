"""Publish the lineup to Channels DVR by replacing the text of its custom M3U sources (one TV, one radio)."""

import logging
from dataclasses import dataclass, field
from functools import cached_property

from sat_to_ip import m3u
from sat_to_ip.channels_dvr.client import ChannelsDVRClient
from sat_to_ip.config import Section
from sat_to_ip.exceptions import CheckError
from sat_to_ip.model import Channel, GuideMatch, Kind, Lineup, PublishResult

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8089"


@dataclass(frozen=True)
class Source:
    """A Channels DVR custom M3U source that receives the channels of one kind."""

    kind: Kind
    source_id: str
    display_name: str


@dataclass
class ChannelsDVRPublisher:
    name: str
    url: str
    sid: str | None
    guide: str | None
    sources: tuple[Source, ...]
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
            entries = [_entry(ch, guide) for ch in channels if guide is None or ch.key in guide.refs]
            if guide is not None and not entries:
                raise CheckError(
                    f"Refusing to empty Channels DVR source {src.source_id!r}: none of the {len(channels)} "
                    f"{src.kind} channels matched guide {guide.source!r}"
                )
            playlists.append((src, entries))

        for src, entries in playlists:
            if dry_run:
                logger.info(f"Dry run: would push {len(entries)} {src.kind} channels to {src.source_id}")
            else:
                self.client.update_m3u_source(src.source_id, src.display_name, m3u.render(entries))
        detail = "; ".join(f"{src.kind}: {len(entries)} → {src.source_id}" for src, entries in playlists)
        return PublishResult(self.name, sum(len(e) for _, e in playlists), detail + (" (dry run)" if dry_run else ""))


def _entry(ch: Channel, guide: GuideMatch | None) -> m3u.Entry:
    if guide is None:
        return m3u.Entry(ch.name, ch.stream_url)
    ref = guide.refs[ch.key]
    attrs = (
        ("channel-id", ref.guide_id),
        ("tvc-guide-stationid", ref.station_id or ""),
        ("tvg-name", ref.display_name or ch.name),
    )
    return m3u.Entry(ch.name, ch.stream_url, attrs)


def from_config(section: Section) -> ChannelsDVRPublisher:
    sources = []
    kinds: tuple[Kind, ...] = ("tv", "radio")
    for kind in kinds:
        source_id = section.get_optional_str(f"{kind}_source")
        display_name = section.get_optional_str(f"{kind}_display_name")
        if source_id:
            sources.append(Source(kind, source_id, display_name or source_id))
        elif display_name:
            raise section.error(f"{kind}_display_name", f"set {kind}_source too, or remove this")
    if not sources:
        raise section.error("tv_source", "set tv_source and/or radio_source (the Channels DVR M3U source IDs)")
    return ChannelsDVRPublisher(
        name=section.name,
        url=section.get_url("url", DEFAULT_URL),
        sid=section.get_secret("sid_env", required=False),
        guide=section.get_optional_str("guide"),
        sources=tuple(sources),
    )
