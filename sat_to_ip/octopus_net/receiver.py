"""The Digital Devices Octopus NET as a Receiver: KingOfSat transponders + rescan, its saved (DMS) channel list,
and its M3U of stream URLs."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sat_to_ip import m3u
from sat_to_ip.config import Section
from sat_to_ip.exceptions import IntegrationError
from sat_to_ip.kingofsat.client import KingOfSatClient
from sat_to_ip.model import Channel, ChannelRef, Kind, Lineup
from sat_to_ip.octopus_net.channels import ch_freq, channel_key, log_scan_diff, mhz
from sat_to_ip.octopus_net.client import OctopusChannel, OctopusClient
from sat_to_ip.octopus_net.transponders import build_upload_payload, format_source

logger = logging.getLogger(__name__)


def _kind(ch: OctopusChannel) -> Kind:
    return "radio" if ch.get("type") == "audio" else "tv"  # Octopus NET types are video/audio


def _ref(ch: OctopusChannel) -> ChannelRef:
    return ChannelRef(channel_key(ch), ch.get("name", ""), _kind(ch), mhz(ch_freq(ch)))


def _log_tuners(client: OctopusClient) -> None:
    """After an empty scan, show each input's signal so a dead dish or cable stands out from a bad transponder list."""
    try:
        tuners = client.get_tuner_status()
        lines = [
            f"Input {t.get('Input', '?')}: {'locked' if t.get('Lock') else 'no lock'}, "
            f"strength {int(t.get('Strength', 0)) / 1000:.1f} dBm, SNR {int(t.get('SNR', 0)) / 1000:.1f} dB"
            for t in tuners
        ]
    except (IntegrationError, TypeError, ValueError) as exc:  # only explains the empty scan; never fails the run
        logger.warning(f"Octopus NET: the scan found no channels, and its tuner status couldn't be read: {exc}")
        return
    logger.warning("Octopus NET: the scan found no channels. Tuner status:\n   " + "\n   ".join(lines or ["(none)"]))


@dataclass
class OctopusNetReceiver:
    host: str
    password: str = field(repr=False)
    username: str = "admin"
    positions: list[str] = field(default_factory=lambda: ["28.2E"])
    kingofsat_filter: str = "Clear"
    kingofsat_language: str = "eng"
    scan_timeout: int = 600
    scan_poll_interval: int = 1
    name: str = "octopus_net"
    _client: OctopusClient | None = field(default=None, init=False, repr=False)
    # Raw Octopus NET records from the last read, by key: saving needs the whole record, not just the key
    _known: dict[str, OctopusChannel] = field(default_factory=dict, init=False, repr=False)

    def _octopus(self) -> OctopusClient:
        if self._client is None:
            base = self.host if self.host.startswith(("http://", "https://")) else f"http://{self.host}"
            self._client = OctopusClient.login(base, self.username, self.password)
        return self._client

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
        if not after:
            _log_tuners(client)
        log_scan_diff(before, after)
        if len(sources) == len(self.positions):  # with a position missing, its channels would all look moved
            self._retune_moved(client, after)

    def _retune_moved(self, client: OctopusClient, scanned: list[OctopusChannel]) -> None:
        """Swap a saved channel the scan no longer found for the same channel the scan found elsewhere.

        The Octopus NET's serviceid includes the frequency, so a channel that moved comes back from the scan
        as a new record: the same name and DVB service ID, on another frequency. Only an unambiguous match is
        used. KingOfSat can still list the old frequency (GB News left 11670 while it stayed listed), so what
        counts is what the scan found, not what KingOfSat lists.
        """
        found = {str(ch.get("serviceid")) for ch in scanned}
        records, moved = [], 0
        for i, saved in enumerate(client.get_dms_channels()):
            old_mhz = mhz(ch_freq(saved))
            if old_mhz is not None and str(saved.get("serviceid")) not in found:
                same = [
                    ch
                    for ch in scanned
                    if ch.get("name") == saved.get("name")
                    and ch.get("dvb_serviceid") == saved.get("dvb_serviceid")
                    and mhz(ch_freq(ch)) is not None
                ]
                if len(same) == 1:
                    logger.info(f"   Retuning {saved.get('name')!r}: {old_mhz:g} → {mhz(ch_freq(same[0])):g} MHz")
                    saved, moved = same[0].copy(), moved + 1
            saved["position"] = i
            records.append(saved)
        if moved:
            client.save_channels(records)

    # SupportsSelection -------------------------------------------------------------------------

    def _fetch(self) -> tuple[list[OctopusChannel], list[OctopusChannel]]:
        """(DMS channels, scanned channels not in the DMS), live; remembered for save_selection."""
        client = self._octopus()
        dms = client.get_dms_channels()
        saved = {channel_key(ch) for ch in dms}
        # a saved record without a frequency can't be told apart from a scanned one: hide its service ID
        no_freq = {str(ch.get("serviceid")) for ch in dms if mhz(ch_freq(ch)) is None}
        available = [
            ch
            for ch in client.get_available_channels([])
            if channel_key(ch) not in saved and str(ch.get("serviceid")) not in no_freq
        ]
        self._known = {channel_key(ch): ch for ch in dms + available}
        return dms, available

    def selection_candidates(self) -> tuple[list[ChannelRef], list[ChannelRef]]:
        dms, available = self._fetch()
        return [_ref(ch) for ch in dms], [_ref(ch) for ch in available]

    def save_selection(self, channels: Sequence[ChannelRef]) -> None:
        if any(ref.key not in self._known for ref in channels):
            self._fetch()  # the refs came from elsewhere, or the Octopus NET changed since
        missing = [ref.key for ref in channels if ref.key not in self._known]
        if missing:
            raise IntegrationError(f"Octopus NET: channel(s) not found, nothing saved: {', '.join(missing)}")
        records = []
        for i, ref in enumerate(channels):
            record = self._known[ref.key].copy()
            record["position"] = i
            records.append(record)
        self._octopus().save_channels(records)

    # Receiver ----------------------------------------------------------------------------------

    def lineup(self) -> Lineup:
        client = self._octopus()
        dms = client.get_dms_channels()
        # Octopus NET M3U labels are DMS names; channels sharing a name take that name's entries in order
        by_label: dict[str, list[m3u.Entry]] = {}
        for entry in m3u.parse(client.download_m3u()):
            by_label.setdefault(entry.label, []).append(entry)

        channels: list[Channel] = []
        for ch in dms:
            name = ch.get("name", "")
            entries = by_label.get(name)
            if not entries:
                logger.warning(f"   {name!r} is saved on the Octopus NET but missing from its M3U; left out")
                continue
            channels.append(
                Channel(
                    key=channel_key(ch),
                    name=name,
                    kind=_kind(ch),
                    stream_url=entries.pop(0).url,
                    frequency_mhz=mhz(ch_freq(ch)),
                )
            )
        return Lineup(tuple(channels))


def from_config(section: Section) -> OctopusNetReceiver:
    password = section.get_secret("password_env")
    assert password is not None  # required=True raises instead of returning None
    return OctopusNetReceiver(
        host=section.get_str("host"),
        password=password,
        username=section.get_str("username", "admin"),
        positions=section.get_str_list("positions", ["28.2E"]),
        kingofsat_filter=section.get_str("kingofsat_filter", "Clear"),
        kingofsat_language=section.get_str("kingofsat_language", "eng"),
        scan_timeout=section.get_int("scan_timeout", 600, minimum=1),
        scan_poll_interval=section.get_int("scan_poll_interval", 1, minimum=1),
    )
