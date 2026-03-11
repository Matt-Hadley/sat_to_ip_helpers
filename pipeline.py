#!/usr/bin/env python3
"""
End-to-end pipeline: KingOfSat → Octopus → Channels DVR

Steps:
  1  Scrape transponders from KingOfSat
  2  Upload transponder list to Octopus
  3  Trigger Octopus channel scan and wait for completion
  4  Add discovered channels to Octopus DMS
  5  Download M3U from Octopus
  6  Fetch Gracenote station data from Channels DVR
  7  Enrich M3U with Gracenote metadata
  8  Push enriched M3U back to Channels DVR source

State from each step is saved to .pipeline_state/ so you can re-run
individual steps without repeating earlier ones.

Secrets via env vars (preferred) or CLI flags:
    OCTOPUS_PASSWORD
    CHANNELS_DVR_SID

Examples:
  # Run the full pipeline:
  python pipeline.py --octopus-host octopus.local --source-id MySource

  # Trigger an Octopus scan only (step 3):
  python pipeline.py --steps 3 --octopus-host octopus.local --positions 28.2E

  # Re-run enrichment and push using the built-in UK mappings (steps 7-8):
  python pipeline.py --steps 7 8 --source-id MySource --mapping-region uk
"""

import argparse
import json
import logging
import os
import sys

import requests
import urllib3

from channels_dvr.client import ChannelsDVRClient
from king_of_sat_scraper.client import KingOfSatClient
from m3u.enrichment import build_gracenote_lookups, enrich_m3u_text
from octopus_api.client import OctopusClient
from octopus_api.transponders import build_upload_payload, format_source
from utils_pipeline import (
    ConfigurationError,
    PipelineError,
    StateError,
    StepError,
    available_regions,
    filter_channels,
    interactive_dms_editor,
    load_region_mappings,
    log_scan_diff,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
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
    if not sid:
        raise ConfigurationError("Set CHANNELS_DVR_SID env var or pass --channels-dvr-sid")
    return ChannelsDVRClient.with_sid(
        host=args.channels_dvr_host,
        port=args.channels_dvr_port,
        sid=sid,
    )


def _king_of_sat(args) -> KingOfSatClient:
    return KingOfSatClient.create(base_url=args.kos_base_url)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_1(args, state: dict) -> None:
    """Scrape transponders from KingOfSat."""
    logger.info("▶  Step 1: Scraping transponders from KingOfSat")
    client = state.setdefault("king_of_sat", _king_of_sat(args))
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
    client = state.setdefault("octopus", _octopus(args))
    client.upload_transponders(payload)
    logger.info("✅  Step 2 done")


def step_3(args, state: dict) -> None:
    """Trigger channel scan and wait for completion."""
    logger.info("▶  Step 3: Triggering channel scan")
    client = state.setdefault("octopus", _octopus(args))
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
    client = state.setdefault("octopus", _octopus(args))
    available = client.get_available_channels([])

    if not args.add_channels and sys.stdin.isatty():
        chosen = interactive_dms_editor([], available)
        if chosen is None:
            logger.info("   Cancelled — DMS unchanged.")
            return
        for i, ch in enumerate(chosen):
            ch["position"] = i
        client.save_channels(chosen)
        save_state(args.state_dir, "dms_channels", chosen)
        state["dms_channels"] = chosen
        logger.info(f"✅  Step 4 done — {len(chosen)} channels saved to DMS")
        return

    if not args.add_channels:
        logger.info("   Channels available to add:")
        for ch in available:
            freq = ch.get("frequency") or ch.get("freq", "?")
            logger.info(f"     {ch.get('name', '?')}  freq={freq}  type={ch.get('type', '?')}")
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

    to_add, unmatched = filter_channels(available, _resolve_add_channels_spec(args.add_channels))
    for entry in unmatched:
        logger.warning(f"   ⚠️  No channel found matching: {entry!r}")

    if not to_add:
        logger.info("   Nothing matched --add-channels spec.")
        return

    for i, ch in enumerate(to_add):
        ch["position"] = i
    client.save_channels(to_add)
    save_state(args.state_dir, "dms_channels", to_add)
    state["dms_channels"] = to_add
    logger.info(f"✅  Step 4 done — {len(to_add)} channels saved to DMS")


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


def _split_m3u(m3u_text: str, dms_channels: list[dict]) -> tuple[str, str]:
    """Split an M3U into TV and radio playlists, preserving dms_channels order.

    Channel labels in the Octopus M3U match DMS channel names, so we parse the
    M3U into a lookup dict then emit entries in dms_channels list order.
    """
    # Parse all M3U entries keyed by channel label
    entries: dict[str, tuple[str, str]] = {}
    lines = m3u_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            label = line.split(",", 1)[1].strip()
            url = lines[i + 1].strip() if i + 1 < len(lines) else ""
            entries[label] = (line, url)
            i += 2
        else:
            i += 1

    tv_lines = ["#EXTM3U"]
    radio_lines = ["#EXTM3U"]
    for ch in dms_channels:
        name = ch.get("name", "")
        if name not in entries:
            continue
        extinf, url = entries[name]
        if ch.get("type") == "audio":
            radio_lines.extend([extinf, url])
        else:
            tv_lines.extend([extinf, url])

    return "\n".join(tv_lines) + "\n", "\n".join(radio_lines) + "\n"


def step_5(args, state: dict) -> None:
    """Download M3U from Octopus and split into TV and radio."""
    logger.info("▶  Step 5: Downloading M3U from Octopus")
    client = state.setdefault("octopus", _octopus(args))
    m3u_text = client.download_m3u()

    dms = state.get("dms_channels") or load_state(args.state_dir, "dms_channels")
    tv_m3u, radio_m3u = _split_m3u(m3u_text, dms)

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
    client = state.setdefault("channels_dvr", _channels_dvr(args))
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
    client = state.setdefault("channels_dvr", _channels_dvr(args))

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

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--steps",
        type=int,
        nargs="+",
        choices=ALL_STEPS,
        metavar="N",
        help="Steps to run (default: all). E.g. --steps 3 or --steps 7 8",
    )
    p.add_argument(
        "--state-dir",
        default=".pipeline_state",
        metavar="DIR",
        help="Directory for intermediate state files (default: .pipeline_state)",
    )

    g = p.add_argument_group("Step 1 — Scrape KingOfSat transponders")
    g.add_argument(
        "--positions",
        nargs="+",
        default=["28.2E"],
        metavar="POS",
        help="Satellite position(s) to scrape, e.g. 28.2E 13.0E (default: 28.2E)",
    )
    g.add_argument(
        "--kos-filter", default="Clear", metavar="FILTER", help="Clear (FTA only), All, or Encrypted (default: Clear)"
    )
    g.add_argument("--kos-cl", default="eng", metavar="LANG", help="Channel language filter (default: eng)")
    g.add_argument(
        "--kos-base-url", default="https://en.kingofsat.net/freqs.php", metavar="URL", help=argparse.SUPPRESS
    )

    g = p.add_argument_group("Octopus connection — required for steps 2, 3, 4, 5")
    g.add_argument(
        "--octopus-host",
        default=None,
        metavar="HOST",
        help="Hostname or IP of the Octopus NET device (e.g. octopus.local)",
    )
    g.add_argument("--octopus-user", default="admin", metavar="USER")
    g.add_argument("--octopus-password", default=None, metavar="PASS", help="Overrides OCTOPUS_PASSWORD env var")

    g = p.add_argument_group("Step 3 — Scan options")
    g.add_argument(
        "--scan-poll-interval", type=int, default=1, metavar="SEC", help="How often to poll scan status (default: 1)"
    )
    g.add_argument(
        "--scan-timeout",
        type=int,
        default=600,
        metavar="SEC",
        help="Give up waiting for scan after this many seconds (default: 600)",
    )

    g = p.add_argument_group("Step 4 — Add channels to DMS")
    g.add_argument(
        "--add-channels",
        default=None,
        metavar="SPEC",
        help=(
            "Which channels to add. Options: "
            "all | video | audio | "
            "comma-separated names ('BBC One HD,ITV HD') | "
            "name@frequency in MHz ('BBC One HD@10773,ITV HD@11386') | "
            "path to a .txt file with one name per line. "
            "Omit to use the interactive editor (if a TTY) or list available channels."
        ),
    )

    g = p.add_argument_group("Channels DVR connection — required for steps 6, 8")
    g.add_argument(
        "--channels-dvr-host",
        default="localhost",
        metavar="HOST",
        help="Hostname of Channels DVR server (default: localhost)",
    )
    g.add_argument(
        "--channels-dvr-port", type=int, default=8089, metavar="PORT", help="Channels DVR port (default: 8089)"
    )
    g.add_argument(
        "--channels-dvr-sid", default=None, metavar="SID", help="SID session cookie. Overrides CHANNELS_DVR_SID env var"
    )

    g = p.add_argument_group("Step 6 — Gracenote options")
    g.add_argument(
        "--station-list",
        default="GBR-1000193-DEFAULT",
        metavar="NAME",
        help="Gracenote station list name (default: GBR-1000193-DEFAULT)",
    )

    g = p.add_argument_group("Step 7 — M3U enrichment options")
    g.add_argument(
        "--mapping-region",
        default="uk",
        metavar="REGION",
        help=(
            f"Region to use for built-in Name→Callsign mappings "
            f"(available: {', '.join(available_regions()) or 'none'}). "
            f"Default: uk"
        ),
    )

    g = p.add_argument_group("Step 8 — Push to Channels DVR")
    g.add_argument("--source-id", default=None, metavar="ID", help="Channels DVR source ID for TV channels")
    g.add_argument(
        "--source-display-name",
        default=None,
        metavar="NAME",
        help="Display name for the TV source (defaults to --source-id)",
    )
    g.add_argument("--source-id-radio", default=None, metavar="ID", help="Channels DVR source ID for radio channels")
    g.add_argument(
        "--source-display-name-radio",
        default=None,
        metavar="NAME",
        help="Display name for the radio source (defaults to --source-id-radio)",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()
    steps_to_run = sorted(args.steps) if args.steps else ALL_STEPS
    logger.info(f"Running steps: {steps_to_run}")
    state: dict = {}
    try:
        for n in steps_to_run:
            STEPS[n](args, state)
    except PipelineError as exc:
        sys.exit(f"❌  {exc}")
    except TimeoutError as exc:
        sys.exit(f"❌  Step timed out: {exc}\n   Try increasing --scan-timeout")
    except requests.exceptions.ConnectionError as exc:
        sys.exit(f"❌  Connection error — is the host reachable?\n   {exc}")
    except requests.exceptions.HTTPError as exc:
        sys.exit(f"❌  HTTP error: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error")
        sys.exit(f"❌  An unexpected error occurred: {exc}")

    logger.info("🎉  Pipeline complete")


if __name__ == "__main__":
    main()
