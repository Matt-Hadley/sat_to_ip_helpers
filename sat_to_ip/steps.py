"""The 8 pipeline steps, their safety checks, and the state files that let you re-run single steps."""

import json
import logging
import os
import sys

from sat_to_ip.channels import ch_freq, compute_scan_diff, filter_channels, log_scan_diff
from sat_to_ip.channels_dvr.client import ChannelsDVRClient
from sat_to_ip.dms_editor import interactive_dms_editor
from sat_to_ip.exceptions import ConfigurationError, StateError, StepError
from sat_to_ip.kingofsat.client import KingOfSatClient
from sat_to_ip.m3u.enrichment import build_gracenote_lookups, enrich_m3u_text, split_m3u
from sat_to_ip.m3u.mappings import load_region_mappings
from sat_to_ip.octopus.client import OctopusClient
from sat_to_ip.octopus.transponders import build_upload_payload, format_source

logger = logging.getLogger(__name__)

ALL_STEPS = list(range(1, 9))

# ---------------------------------------------------------------------------
# State helpers — each step persists its output so individual steps can be
# re-run without repeating earlier ones.
# ---------------------------------------------------------------------------


def _state_path(state_dir: str, key: str, text: bool = False) -> str:
    return os.path.join(state_dir, f"{key}.{'m3u' if text else 'json'}")


def save_state(state_dir: str, key: str, data, text: bool = False) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(_state_path(state_dir, key, text), "w", encoding="utf-8") as f:
        f.write(data if text else json.dumps(data, indent=2))


def load_state(state_dir: str, key: str, text: bool = False):
    path = _state_path(state_dir, key, text)
    if not os.path.exists(path):
        raise StateError(f"State file not found: {path}\n   Run earlier steps first.")
    with open(path, encoding="utf-8") as f:
        return f.read() if text else json.load(f)


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------


def _octopus(args) -> OctopusClient:
    if not args.octopus_host:
        raise ConfigurationError("--octopus-host is required for Octopus steps (2, 3, 4, 5)")
    password = args.octopus_password or os.environ.get("OCTOPUS_PASSWORD")
    if not password:
        raise ConfigurationError("Set OCTOPUS_PASSWORD env var or pass --octopus-password")
    return OctopusClient.login(
        base_url=f"http://{args.octopus_host}",
        username=args.octopus_user,
        password=password,
    )


def _channels_dvr(args) -> ChannelsDVRClient:
    sid = args.channels_dvr_sid or os.environ.get("CHANNELS_DVR_SID")
    return ChannelsDVRClient.with_sid(
        host=args.channels_dvr_host,
        port=args.channels_dvr_port,
        sid=sid,
    )


def _king_of_sat(args) -> KingOfSatClient:
    return KingOfSatClient.create(base_url=args.kos_base_url)


def _cached(state: dict, key: str, factory, args):
    """Build a client once per run (setdefault would call the factory, and log in, every time)."""
    if key not in state:
        state[key] = factory(args)
    return state[key]


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_1(args, state: dict) -> None:
    """Scrape transponders from KingOfSat."""
    logger.info("▶  Step 1: Scraping transponders from KingOfSat")
    client = _cached(state, "king_of_sat", _king_of_sat, args)
    sources = []
    for pos in args.positions:
        try:
            transponders = client.fetch_transponders(pos, channel_filter=args.kos_filter, cl=args.kos_cl)
        except Exception as exc:
            logger.error(f"Failed to fetch transponders for {pos}: {exc}")
            continue
        if not transponders:
            logger.warning(f"No transponders found for {pos}")
            continue
        satellite = transponders[0].satellite
        sources.append(format_source(transponders, pos, satellite))
        logger.info(f"   {pos}: {len(transponders)} transponders ({satellite})")

    if not sources:
        raise StepError("No transponder data scraped. Aborting.")

    payload = build_upload_payload(sources)
    save_state(args.state_dir, "transponders", payload)
    state["transponders"] = payload
    logger.info(f"✅  Step 1 done — {len(sources)} source(s)")


def step_2(args, state: dict) -> None:
    """Upload transponder list to Octopus."""
    logger.info("▶  Step 2: Uploading transponders to Octopus")
    payload = state.get("transponders") or load_state(args.state_dir, "transponders")
    client = _cached(state, "octopus", _octopus, args)
    client.upload_transponders(payload)
    logger.info("✅  Step 2 done")


def step_3(args, state: dict) -> None:
    """Trigger channel scan and wait for completion."""
    logger.info("▶  Step 3: Triggering channel scan")
    client = _cached(state, "octopus", _octopus, args)
    pre_available = client.get_available_channels([])
    logger.info(f"   Pre-scan: {len(pre_available)} channels available")
    client.start_scan(args.positions)
    client.poll_scan_until_complete(
        interval=args.scan_poll_interval,
        timeout=args.scan_timeout,
    )
    post_available = client.get_available_channels([])
    logger.info(f"   Post-scan: {len(post_available)} channels found")
    log_scan_diff(pre_available, post_available)
    logger.info("✅  Step 3 done")


def step_4(args, state: dict) -> None:
    """Add channels from the last scan to the Octopus DMS."""
    logger.info("▶  Step 4: Adding channels to DMS")
    client = _cached(state, "octopus", _octopus, args)
    dms = client.get_dms_channels()
    available = client.get_available_channels([str(ch["serviceid"]) for ch in dms if "serviceid" in ch])

    if not args.add_channels and sys.stdin.isatty():
        # Pass the current DMS so it starts ticked — saving replaces the whole DMS list
        chosen = interactive_dms_editor(dms, available)
        if chosen is None:
            logger.info("   Cancelled — DMS unchanged.")
            return
        for i, ch in enumerate(chosen):
            ch["position"] = i
        client.save_channels(chosen)
        save_state(args.state_dir, "dms_channels", chosen)
        logger.info(f"✅  Step 4 done — {len(chosen)} channels saved to DMS")
        return

    if not args.add_channels:
        logger.info("   Channels available to add:")
        for ch in available:
            logger.info(f"     {ch.get('name', '?')}  freq={ch_freq(ch)}  type={ch.get('type', '?')}")
        logger.info("")
        logger.info("   Use --add-channels to specify which channels to add:")
        logger.info("     all                       add every available channel")
        logger.info("     video                     all video channels")
        logger.info("     audio                     all audio channels")
        logger.info("     'BBC One HD,ITV HD'       by name (comma-separated)")
        logger.info("     'BBC One HD@10773'        by name and frequency in MHz")
        logger.info("")
        logger.info("   Step 4 skipped. Re-run with --steps 4 --add-channels <spec>")
        return

    to_add, unmatched = filter_channels(dms + available, _resolve_add_channels_spec(args.add_channels))
    for entry in unmatched:
        logger.warning(f"   ⚠️  No channel found matching: {entry!r}")

    if not to_add:
        logger.info("   Nothing matched --add-channels spec.")
        return

    _check_dms_removals(args, dms, to_add)
    for i, ch in enumerate(to_add):
        ch["position"] = i
    client.save_channels(to_add)
    save_state(args.state_dir, "dms_channels", to_add)
    logger.info(f"✅  Step 4 done — {len(to_add)} channels saved to DMS")


def _check_dms_removals(args, current: list[dict], new: list[dict]) -> None:
    """Refuse to save a DMS list that drops more than --max-removed of the channels on the Octopus now.

    Guards unattended runs: a bad scan (rain, dish fault) matches fewer channels and would otherwise
    shrink the DMS, and then the Channels DVR lineup.
    """
    diff = compute_scan_diff(current, new)
    logger.info(f"   DMS change: +{len(diff.added)} / -{len(diff.removed)} channels")
    for ch in diff.removed:
        logger.warning(f"     - {ch.get('name', '?')}  freq={ch_freq(ch)}")
    if len(diff.removed) > args.max_removed and not args.allow_removals:
        raise StepError(
            f"Refusing to remove {len(diff.removed)} channels from the DMS (limit: --max-removed {args.max_removed}).\n"
            "   Check the scan, or re-run with --allow-removals if this is intended."
        )


def _resolve_add_channels_spec(spec: str) -> str:
    """Return the spec string, loading from a file if spec is an existing path.

    File format: one channel name (or name@freq) per line; blank lines and
    lines starting with '#' are ignored.  The result is a comma-joined string
    that filter_channels() can consume unchanged.
    """
    if os.path.isfile(spec):
        with open(spec, encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        logger.info(f"   Loaded {len(names)} channel names from {spec}")
        return ",".join(names)
    return spec


def step_5(args, state: dict) -> None:
    """Download M3U from Octopus and split into TV and radio."""
    logger.info("▶  Step 5: Downloading M3U from Octopus")
    client = _cached(state, "octopus", _octopus, args)
    m3u_text = client.download_m3u()

    dms = client.get_dms_channels()
    tv_m3u, radio_m3u = split_m3u(m3u_text, dms)

    tv_count = tv_m3u.count("#EXTINF")
    radio_count = radio_m3u.count("#EXTINF")

    save_state(args.state_dir, "octopus_m3u_tv", tv_m3u, text=True)
    save_state(args.state_dir, "octopus_m3u_radio", radio_m3u, text=True)
    state["octopus_m3u_tv"] = tv_m3u
    state["octopus_m3u_radio"] = radio_m3u
    logger.info(f"✅  Step 5 done — TV: {tv_count} channels, Radio: {radio_count} channels")


def step_6(args, state: dict) -> None:
    """Fetch Gracenote station data from Channels DVR."""
    logger.info("▶  Step 6: Fetching Gracenote data")
    client = _cached(state, "channels_dvr", _channels_dvr, args)
    gracenote_data = client.get_gracenote(args.station_list)
    save_state(args.state_dir, "gracenote", gracenote_data)
    state["gracenote"] = gracenote_data
    logger.info(f"✅  Step 6 done — {len(gracenote_data)} stations")


def step_7(args, state: dict) -> None:
    """Enrich TV and radio M3Us with Gracenote metadata."""
    logger.info("▶  Step 7: Enriching M3U with Gracenote data")
    tv_m3u = state.get("octopus_m3u_tv") or load_state(args.state_dir, "octopus_m3u_tv", text=True)
    radio_m3u = state.get("octopus_m3u_radio") or load_state(args.state_dir, "octopus_m3u_radio", text=True)
    gracenote_data = state.get("gracenote") or load_state(args.state_dir, "gracenote")

    explicit_mappings = load_region_mappings(args.mapping_region) if args.mapping_region else {}
    lookups = build_gracenote_lookups(gracenote_data)

    tv_result = enrich_m3u_text(tv_m3u, explicit_mappings, lookups)
    radio_result = enrich_m3u_text(radio_m3u, explicit_mappings, lookups)

    # Checked before saving so a later --steps 8 can't push a gutted playlist either
    enriched = tv_result.enriched + radio_result.enriched
    total = enriched + tv_result.skipped + radio_result.skipped
    if total and enriched / total < args.min_guide_match:
        raise StepError(
            f"Only {enriched}/{total} channels matched Gracenote (minimum: --min-guide-match {args.min_guide_match}).\n"
            "   Check the Gracenote station list (step 6) before pushing to Channels DVR."
        )

    save_state(args.state_dir, "enriched_m3u_tv", tv_result.text, text=True)
    save_state(args.state_dir, "enriched_m3u_radio", radio_result.text, text=True)
    state["enriched_m3u_tv"] = tv_result.text
    state["enriched_m3u_radio"] = radio_result.text

    for ch in tv_result.skipped_channels + radio_result.skipped_channels:
        logger.warning(f"   ⚠️  No Gracenote match, channel excluded from M3U: {ch!r}")
    logger.info(
        f"✅  Step 7 done — "
        f"TV: {tv_result.enriched} enriched, {tv_result.skipped} skipped  |  "
        f"Radio: {radio_result.enriched} enriched, {radio_result.skipped} skipped"
    )


def step_8(args, state: dict) -> None:
    """Push enriched TV and radio M3Us to Channels DVR."""
    logger.info("▶  Step 8: Updating Channels DVR source(s)")
    if not args.source_id and not args.source_id_radio:
        raise ConfigurationError("--source-id and/or --source-id-radio is required for step 8")
    client = _cached(state, "channels_dvr", _channels_dvr, args)

    if args.source_id:
        tv_m3u = state.get("enriched_m3u_tv") or load_state(args.state_dir, "enriched_m3u_tv", text=True)
        client.update_m3u_source(
            source_id=args.source_id,
            display_name=args.source_display_name or args.source_id,
            m3u_text=tv_m3u,
        )
        logger.info(f"   TV source updated: {args.source_id}")

    if args.source_id_radio:
        radio_m3u = state.get("enriched_m3u_radio") or load_state(args.state_dir, "enriched_m3u_radio", text=True)
        client.update_m3u_source(
            source_id=args.source_id_radio,
            display_name=args.source_display_name_radio or args.source_id_radio,
            m3u_text=radio_m3u,
        )
        logger.info(f"   Radio source updated: {args.source_id_radio}")

    logger.info("✅  Step 8 done")


STEPS = {1: step_1, 2: step_2, 3: step_3, 4: step_4, 5: step_5, 6: step_6, 7: step_7, 8: step_8}
