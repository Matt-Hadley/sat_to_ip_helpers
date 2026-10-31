"""The Digital Devices Octopus NET as a Receiver: KingOfSat transponders + rescan, its saved (DMS) channel list,
and its M3U of stream URLs."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sat_to_ip import m3u
from sat_to_ip.checks import NEEDS_ATTENTION
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


def _service_id(ch: OctopusChannel) -> str | None:
    sid = ch.get("dvb_serviceid")
    return None if sid is None else str(sid)


def _ref(ch: OctopusChannel) -> ChannelRef:
    return ChannelRef(channel_key(ch), ch.get("name", ""), _kind(ch), mhz(ch_freq(ch)), _service_id(ch))


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
        logger.warning(
            f"Octopus NET: the scan found no channels, and its tuner status couldn't be read: {exc}",
            extra=NEEDS_ATTENTION,
        )
        return
    logger.warning(
        "Octopus NET: the scan found no channels. Tuner status:\n   " + "\n   ".join(lines or ["(none)"]),
        extra=NEEDS_ATTENTION,
    )


def _candidates(
    ch: OctopusChannel,
    scanned: list[OctopusChannel],
    found: set[str],
    timeshares: Sequence[frozenset[str]],
    swept: set[float | None],
) -> list[OctopusChannel] | None:
    """None when the scan found the saved channel where it was; else the scanned channels it may have moved to.

    The Octopus NET's serviceid includes the frequency, so a channel that moved comes back from the scan
    as a new record: the same DVB service ID on another frequency, under the same name or (renamed in the
    same week) another. Another name only counts when the scan found its old frequency (`swept`): the channel
    really left it, rather than a missed transponder making an unrelated service with that ID (on another
    satellite) look like it. A timeshare's DVB service ID changes with the name on air, so it's looked for first
    on its own frequency, under any of its names.
    """
    here = mhz(ch_freq(ch))
    if here is None or str(ch.get("serviceid")) in found:
        return None
    names = next((ts for ts in timeshares if ch.get("name", "").lower() in ts), frozenset())
    on_air = [new for new in scanned if new.get("name", "").lower() in names and mhz(ch_freq(new)) == here]
    sid = ch.get("dvb_serviceid")
    elsewhere = [
        new for new in scanned if sid is not None and new.get("dvb_serviceid") == sid and mhz(ch_freq(new)) is not None
    ]
    return (
        sorted(on_air, key=channel_key)[:1]  # the one a channel list entry would take
        or [new for new in elsewhere if new.get("name") == ch.get("name")]
        or (elsewhere if here in swept else [])
    )


def _lost(
    saved: list[OctopusChannel], scanned: list[OctopusChannel], timeshares: Sequence[frozenset[str]]
) -> list[OctopusChannel]:
    """Saved channels the scan found neither where they were nor, unambiguously, anywhere else."""
    found = {str(ch.get("serviceid")) for ch in scanned}
    swept = {mhz(ch_freq(ch)) for ch in scanned}
    return [
        ch
        for ch in saved
        if (same := _candidates(ch, scanned, found, timeshares, swept)) is not None and len(same) != 1
    ]


def _retune_moved(
    saved: list[OctopusChannel], scanned: list[OctopusChannel], timeshares: Sequence[frozenset[str]]
) -> list[OctopusChannel]:
    """The saved channels as the scan found them now: renamed ones under their new name, and each one the scan
    no longer found swapped for the same channel the scan found elsewhere.

    Only an unambiguous match is used; a channel without one is kept as it was. KingOfSat can still list the
    old frequency (GB News left 11670 while it stayed listed), so what counts is what the scan found.
    """
    found = {str(ch.get("serviceid")): ch for ch in scanned}
    swept = {mhz(ch_freq(ch)) for ch in scanned}
    records: list[OctopusChannel] = []
    for ch in saved:
        fresh = found.get(str(ch.get("serviceid")))
        if fresh is not None:  # found where it was: take the scan's current details, its name included
            if fresh.get("name") != ch.get("name"):
                logger.info(f"   Renamed {ch.get('name')!r} → {fresh.get('name')!r}")
            records.append(fresh)
            continue
        # two saved channels never share a record: not one retuned already, nor one the scan found unmoved
        taken = {str(r.get("serviceid")) for r in [*records, *saved]}
        free = [new for new in scanned if str(new.get("serviceid")) not in taken]
        same = _candidates(ch, free, set(found), timeshares, swept)
        if same is not None:
            old_mhz = mhz(ch_freq(ch))
            if len(same) == 1:
                new_name, new_mhz = same[0].get("name"), mhz(ch_freq(same[0]))
                if new_mhz == old_mhz:  # a timeshare: the other channel is on air
                    logger.info(f"   {ch.get('name')!r} is on air as {new_name!r} now")
                else:
                    logger.info(f"   Retuning {ch.get('name')!r}: {old_mhz:g} → {new_mhz:g} MHz")
                ch = same[0]
            else:
                logger.warning(
                    f"   {ch.get('name')!r} is saved on {old_mhz:g} MHz, but the scan didn't find it there"
                    + (f" and found {len(same)} candidates elsewhere" if same else " or anywhere else"),
                    extra=NEEDS_ATTENTION,
                )
        records.append(ch)
    return records


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
    verify_tls: bool = False  # with an https:// host: check its certificate (it ships a self-signed one)
    ca_file: Path | None = None  # with an https:// host: check its certificate against this CA instead
    name: str = "octopus_net"
    _client: OctopusClient | None = field(default=None, init=False, repr=False)
    # Raw Octopus NET records from the last read, by key: saving needs the whole record, not just the key
    _known: dict[str, OctopusChannel] = field(default_factory=dict, init=False, repr=False)

    def _octopus(self) -> OctopusClient:
        if self._client is None:
            base = self.host if self.host.startswith(("http://", "https://")) else f"http://{self.host}"
            verify = str(self.ca_file) if self.ca_file else self.verify_tls
            self._client = OctopusClient.login(base, self.username, self.password, verify)
        return self._client

    # SupportsRefresh ---------------------------------------------------------------------------

    def refresh(self, timeshares: Sequence[frozenset[str]] = ()) -> None:
        kos = KingOfSatClient.create()
        sources = []
        for pos in self.positions:
            try:
                transponders = kos.fetch_transponders(
                    pos, channel_filter=self.kingofsat_filter, cl=self.kingofsat_language
                )
            except Exception as exc:
                logger.warning(f"KingOfSat: failed to fetch transponders for {pos}: {exc}", extra=NEEDS_ATTENTION)
                continue
            if not transponders:
                logger.warning(f"KingOfSat: no transponders found for {pos}", extra=NEEDS_ATTENTION)
                continue
            sources.append(format_source(transponders, pos, transponders[0].satellite))
        if not sources:
            raise IntegrationError(f"KingOfSat: no transponders for any position ({', '.join(self.positions)})")

        client = self._octopus()
        saved = client.get_dms_channels()  # the Octopus NET empties its saved list when it scans: put it back after
        complete = len(sources) == len(self.positions)  # with a position missing, its channels would all look lost
        try:
            client.upload_transponders(build_upload_payload(sources))
            before = client.get_available_channels()
            logger.info(f"   Pre-scan: {len(before)} channels available")
            after = self._scan(client)
            if complete and (lost := _lost(saved, after, timeshares)):
                # A scan can miss a whole transponder (each of three on 2026-10-29 missed different ones): try again
                logger.info(f"   {len(lost)} saved channel(s) not found; scanning once more")
                after = list({str(ch.get("serviceid")): ch for ch in [*after, *self._scan(client)]}.values())
            if not after:
                _log_tuners(client)
            log_scan_diff(before, after)
            if saved:
                records = _retune_moved(saved, after, timeshares) if complete else saved
                client.save_channels([{**ch, "position": i} for i, ch in enumerate(records)])
        except BaseException:  # Ctrl-C too: a stopped scan or save must not leave the box without its channel list
            if saved:
                client.save_channels(saved)
            raise

    def _scan(self, client: OctopusClient) -> list[OctopusChannel]:
        client.start_scan(self.positions)
        client.poll_scan_until_complete(interval=self.scan_poll_interval, timeout=self.scan_timeout)
        found = client.get_available_channels()
        logger.info(f"   Post-scan: {len(found)} channels found")
        return found

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
            for ch in client.get_available_channels()
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
                    stream_url=self._stream_url(entries.pop(0).url),
                    frequency_mhz=mhz(ch_freq(ch)),
                    service_id=_service_id(ch),
                )
            )
        return Lineup(tuple(channels))

    def _stream_url(self, url: str) -> str:
        """The stream URL on the configured host: the Octopus NET's M3U names itself by IP, which can change."""
        host = urlsplit(self.host if "://" in self.host else f"//{self.host}").hostname
        parts = urlsplit(url)
        if not host or not parts.hostname:
            return url
        host = f"[{host}]" if ":" in host else host  # an IPv6 address keeps its brackets
        return urlunsplit(parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host))


def from_config(section: Section) -> OctopusNetReceiver:
    password = section.get_secret("password_env")
    assert password is not None  # required=True raises instead of returning None
    host = section.get_str("host")
    verify_tls = section.get_bool("verify_tls", False)
    ca_file = section.get_optional_path("ca_file")
    for key, used in (("verify_tls", verify_tls), ("ca_file", ca_file)):
        if used and not host.startswith("https://"):
            raise section.error(key, f'only applies to an https:// host; set host = "https://{host.split("://")[-1]}"')
    return OctopusNetReceiver(
        host=host,
        password=password,
        username=section.get_str("username", "admin"),
        positions=section.get_str_list("positions", ["28.2E"]),
        kingofsat_filter=section.get_str("kingofsat_filter", "Clear"),
        kingofsat_language=section.get_str("kingofsat_language", "eng"),
        scan_timeout=section.get_int("scan_timeout", 600, minimum=1),
        scan_poll_interval=section.get_int("scan_poll_interval", 1, minimum=1),
        verify_tls=verify_tls,
        ca_file=ca_file,
    )
