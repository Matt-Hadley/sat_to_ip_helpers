"""The Octopus NET as a Receiver: KingOfSat transponders + rescan, its saved (DMS) channel list, and its M3U."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sat_to_ip import m3u
from sat_to_ip.config import Section
from sat_to_ip.exceptions import IntegrationError
from sat_to_ip.kingofsat.client import KingOfSatClient
from sat_to_ip.model import Channel, ChannelRef, Lineup, SelectionPlan
from sat_to_ip.octopus import dms_editor
from sat_to_ip.octopus.client import OctopusChannel, OctopusClient
from sat_to_ip.octopus.selection import ch_freq, channel_key, filter_channels, log_scan_diff
from sat_to_ip.octopus.transponders import build_upload_payload, format_source

logger = logging.getLogger(__name__)


def _refs(channels: list[OctopusChannel]) -> tuple[ChannelRef, ...]:
    return tuple(ChannelRef(channel_key(ch), ch.get("name", "")) for ch in channels)


def _mhz(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class OctopusReceiver:
    host: str
    password: str = field(repr=False)
    username: str = "admin"
    positions: list[str] = field(default_factory=lambda: ["28.2E"])
    kingofsat_filter: str = "Clear"
    kingofsat_language: str = "eng"
    scan_timeout: int = 600
    scan_poll_interval: int = 1
    name: str = "octopus"
    _client: OctopusClient | None = field(default=None, init=False, repr=False)
    # Raw Octopus dicts from the last plan, by key: saving needs the whole dict, not just the key
    _known: dict[str, OctopusChannel] = field(default_factory=dict, init=False, repr=False)

    def _octopus(self) -> OctopusClient:
        if self._client is None:
            self._client = OctopusClient.login(f"http://{self.host}", self.username, self.password)
        return self._client

    def _fetch(self) -> tuple[list[OctopusChannel], list[OctopusChannel]]:
        """(DMS channels, scanned channels not in the DMS), live; remembered for apply_selection."""
        client = self._octopus()
        dms = client.get_dms_channels()
        available = client.get_available_channels([str(ch["serviceid"]) for ch in dms if "serviceid" in ch])
        self._known = {channel_key(ch): ch for ch in dms + available}
        return dms, available

    # SupportsRefresh ---------------------------------------------------------------------------

    def refresh(self) -> None:
        kos = KingOfSatClient.create()
        sources = []
        for pos in self.positions:
            try:
                transponders = kos.fetch_transponders(
                    pos, channel_filter=self.kingofsat_filter, cl=self.kingofsat_language
                )
            except Exception as exc:
                logger.warning(f"KingOfSat: failed to fetch transponders for {pos}: {exc}")
                continue
            if not transponders:
                logger.warning(f"KingOfSat: no transponders found for {pos}")
                continue
            sources.append(format_source(transponders, pos, transponders[0].satellite))
        if not sources:
            raise IntegrationError(f"KingOfSat: no transponders for any position ({', '.join(self.positions)})")

        client = self._octopus()
        client.upload_transponders(build_upload_payload(sources))
        before = client.get_available_channels([])
        logger.info(f"   Pre-scan: {len(before)} channels available")
        client.start_scan(self.positions)
        client.poll_scan_until_complete(interval=self.scan_poll_interval, timeout=self.scan_timeout)
        after = client.get_available_channels([])
        logger.info(f"   Post-scan: {len(after)} channels found")
        log_scan_diff(before, after)

    # SupportsSelection -------------------------------------------------------------------------

    def plan_selection(self, specs: Sequence[str]) -> SelectionPlan:
        dms, available = self._fetch()
        matched, unmatched = filter_channels(dms + available, specs)
        return SelectionPlan(_refs(dms), _refs(matched), tuple(unmatched))

    def plan_selection_interactive(self) -> SelectionPlan | None:
        dms, available = self._fetch()
        chosen = dms_editor.interactive_dms_editor(dms, available)
        return None if chosen is None else SelectionPlan(_refs(dms), _refs(chosen))

    def apply_selection(self, plan: SelectionPlan) -> None:
        if any(ref.key not in self._known for ref in plan.proposed):
            self._fetch()  # the plan was made elsewhere, or the Octopus changed since
        missing = [ref.key for ref in plan.proposed if ref.key not in self._known]
        if missing:
            raise IntegrationError(f"Octopus: channel(s) not found, nothing saved: {', '.join(missing)}")
        channels = []
        for i, ref in enumerate(plan.proposed):
            ch = self._known[ref.key].copy()
            ch["position"] = i
            channels.append(ch)
        self._octopus().save_channels(channels)

    # Receiver ----------------------------------------------------------------------------------

    def lineup(self) -> Lineup:
        client = self._octopus()
        dms = client.get_dms_channels()
        # Octopus M3U labels are DMS names; channels sharing a name take that name's entries in order
        by_label: dict[str, list[m3u.Entry]] = {}
        for entry in m3u.parse(client.download_m3u()):
            by_label.setdefault(entry.label, []).append(entry)

        channels: list[Channel] = []
        for ch in dms:
            name = ch.get("name", "")
            entries = by_label.get(name)
            if not entries:
                logger.warning(f"   {name!r} is saved on the Octopus but missing from its M3U; left out")
                continue
            sid = ch.get("serviceid")
            channels.append(
                Channel(
                    key=channel_key(ch),
                    name=name,
                    kind="tv" if ch.get("type") == "video" else "radio",
                    stream_url=entries.pop(0).url,
                    number=len(channels) + 1,
                    frequency_mhz=_mhz(ch_freq(ch)),
                    service_id=None if sid is None or sid == "" else str(sid),
                )
            )
        return Lineup(tuple(channels))


def from_config(section: Section) -> OctopusReceiver:
    password = section.get_secret("password_env")
    assert password is not None  # required=True raises instead of returning None
    return OctopusReceiver(
        host=section.get_str("host"),
        password=password,
        username=section.get_str("username", "admin"),
        positions=section.get_str_list("positions", ["28.2E"]),
        kingofsat_filter=section.get_str("kingofsat_filter", "Clear"),
        kingofsat_language=section.get_str("kingofsat_language", "eng"),
        scan_timeout=section.get_int("scan_timeout", 600, minimum=1),
        scan_poll_interval=section.get_int("scan_poll_interval", 1, minimum=1),
    )
