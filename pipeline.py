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

  # Re-run enrichment and push after updating your mapping CSV (steps 7-8):
  python pipeline.py --steps 7 8 --source-id MySource --mapping-csv overrides.csv
"""

import argparse
import json
import logging
import os
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import csv

from channels_dvr.client import ChannelsDVRClient
from king_of_sat_scraper.client import KingOfSatClient
from m3u.enrichment import build_gracenote_lookups, enrich_m3u_text
from octopus_api.client import OctopusClient
from octopus_api.transponders import build_upload_payload, format_source

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
        sys.exit(f"❌  State file not found: {path}\n   Run earlier steps first.")
    with open(path, encoding="utf-8") as f:
        return f.read() if text else json.load(f)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _load_csv_mappings(path: str) -> dict[str, str]:
    """Load a Name→Callsign CSV override file."""
    mappings: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
            name_idx = headers.index("Name")
            callsign_idx = headers.index("Callsign")
        except (ValueError, StopIteration):
            logger.warning(f"Invalid CSV headers in {path}, expected Name,Callsign")
            return mappings
        for row in reader:
            if len(row) > max(name_idx, callsign_idx):
                mappings[row[name_idx].strip()] = row[callsign_idx].strip()
    logger.info(f"Loaded {len(mappings)} channel mappings from {path}")
    return mappings


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------


def _octopus(args) -> OctopusClient:
    if not args.octopus_host:
        sys.exit("❌  --octopus-host is required for Octopus steps (2, 3, 4, 5)")
    password = args.octopus_password or os.environ.get("OCTOPUS_PASSWORD")
    if not password:
        sys.exit("❌  Set OCTOPUS_PASSWORD env var or pass --octopus-password")
    return OctopusClient.login(
        base_url=f"http://{args.octopus_host}",
        username=args.octopus_user,
        password=password,
    )


def _channels_dvr(args) -> ChannelsDVRClient:
    sid = args.channels_dvr_sid or os.environ.get("CHANNELS_DVR_SID")
    if not sid:
        sys.exit("❌  Set CHANNELS_DVR_SID env var or pass --channels-dvr-sid")
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
        sys.exit("❌  No transponder data scraped. Aborting.")

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

    pre_scan = client.get_dms_channels() + client.get_available_channels([])
    logger.info(f"   Pre-scan: {len(pre_scan)} channels found")

    client.start_scan(args.positions)
    client.poll_scan_until_complete(
        interval=args.scan_poll_interval,
        timeout=args.scan_timeout,
    )

    post_scan = client.get_dms_channels() + client.get_available_channels([])
    logger.info(f"   Post-scan: {len(post_scan)} channels found")

    _diff_scan_results(pre_scan, post_scan)
    logger.info("✅  Step 3 done")


def _diff_scan_results(before: list[dict], after: list[dict]) -> None:
    """Log channels added, removed, or changed frequency between two scans."""

    def _ch_freq(ch: dict):
        return ch.get("frequency") or ch.get("freq")

    before_by_id = {ch["serviceid"]: ch for ch in before if "serviceid" in ch}
    after_by_id = {ch["serviceid"]: ch for ch in after if "serviceid" in ch}

    added = [ch for sid, ch in after_by_id.items() if sid not in before_by_id]
    removed = [ch for sid, ch in before_by_id.items() if sid not in after_by_id]
    freq_changed = [
        (before_by_id[sid], ch)
        for sid, ch in after_by_id.items()
        if sid in before_by_id and _ch_freq(before_by_id[sid]) != _ch_freq(ch)
    ]

    if not added and not removed and not freq_changed:
        logger.info("   No changes detected vs previous scan")
        return

    if added:
        logger.info(f"   New channels ({len(added)}):")
        for ch in added:
            logger.info(f"     + {ch.get('name', '?')}  freq={_ch_freq(ch)}  type={ch.get('type', '?')}")

    if removed:
        logger.warning(f"   ⚠️  Channels no longer found ({len(removed)}):")
        for ch in removed:
            logger.warning(f"     - {ch.get('name', '?')}  freq={_ch_freq(ch)}")

    if freq_changed:
        logger.warning(f"   ⚠️  Channels with changed frequency ({len(freq_changed)}):")
        for ch_before, ch_after in freq_changed:
            logger.warning(f"     ~ {ch_after.get('name', '?')}  " f"{_ch_freq(ch_before)} → {_ch_freq(ch_after)}")


def step_4(args, state: dict) -> None:
    """Add discovered channels to the Octopus DMS."""
    logger.info("▶  Step 4: Adding channels to DMS")
    client = state.setdefault("octopus", _octopus(args))

    dms_channels = client.get_dms_channels()
    dms_ids = [ch["serviceid"] for ch in dms_channels]
    logger.info(f"   Existing DMS channels: {len(dms_channels)}")

    available = client.get_available_channels(dms_ids)
    logger.info(f"   Available (not in DMS): {len(available)}")

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

    to_add, unmatched = _filter_channels(available, args.add_channels)
    for entry in unmatched:
        logger.warning(f"   ⚠️  No channel found matching: {entry!r}")
    logger.info(f"   Channels to add: {len(to_add)}")

    if not to_add:
        logger.info("   Nothing matched --add-channels spec.")
        return

    next_pos = max((ch.get("position", 0) for ch in dms_channels), default=-1) + 1
    for i, ch in enumerate(to_add):
        ch["position"] = next_pos + i

    client.save_channels(dms_channels + to_add)
    logger.info("✅  Step 4 done")


def _filter_channels(channels: list[dict], spec: str) -> tuple[list[dict], list[str]]:
    """Return (matched_channels, unmatched_spec_entries).

    spec can be: all | video | audio | comma-separated 'Name' or 'Name@Frequency'
    """
    if spec == "all":
        return channels, []
    if spec == "video":
        return [ch for ch in channels if ch.get("type") == "video"], []
    if spec == "audio":
        return [ch for ch in channels if ch.get("type") == "audio"], []
    # Comma-separated entries of "Name" or "Name@Frequency"
    matches = []
    unmatched = []
    seen_ids: set[str] = set()
    for entry in (e.strip() for e in spec.split(",")):
        found = False
        if "@" in entry:
            name_part, freq_part = entry.split("@", 1)
            name_part = name_part.strip().lower()
            freq_part = freq_part.strip()
            for ch in channels:
                ch_freq = str(ch.get("frequency") or ch.get("freq", ""))
                if ch.get("name", "").lower() == name_part and ch_freq == freq_part:
                    sid = ch.get("serviceid", id(ch))
                    if sid not in seen_ids:
                        matches.append(ch)
                        seen_ids.add(sid)
                        found = True
        else:
            # No frequency specified — use the first match for that name
            name_lower = entry.lower()
            for ch in channels:
                if ch.get("name", "").lower() == name_lower:
                    sid = ch.get("serviceid", id(ch))
                    if sid not in seen_ids:
                        matches.append(ch)
                        seen_ids.add(sid)
                        found = True
                        break  # stop at first match; use Name@Frequency to be explicit
        if not found:
            unmatched.append(entry)
    return matches, unmatched


def step_5(args, state: dict) -> None:
    """Download M3U from Octopus."""
    logger.info("▶  Step 5: Downloading M3U from Octopus")
    client = state.setdefault("octopus", _octopus(args))
    m3u_text = client.download_m3u()
    save_state(args.state_dir, "octopus_m3u", m3u_text, text=True)
    state["octopus_m3u"] = m3u_text
    logger.info(f"✅  Step 5 done — {len(m3u_text)} bytes")


def step_6(args, state: dict) -> None:
    """Fetch Gracenote station data from Channels DVR."""
    logger.info("▶  Step 6: Fetching Gracenote data")
    client = state.setdefault("channels_dvr", _channels_dvr(args))
    gracenote_data = client.get_gracenote(args.station_list)
    save_state(args.state_dir, "gracenote", gracenote_data)
    state["gracenote"] = gracenote_data
    logger.info(f"✅  Step 6 done — {len(gracenote_data)} stations")


def step_7(args, state: dict) -> None:
    """Enrich M3U with Gracenote metadata."""
    logger.info("▶  Step 7: Enriching M3U with Gracenote data")
    m3u_text = state.get("octopus_m3u") or load_state(args.state_dir, "octopus_m3u", text=True)
    gracenote_data = state.get("gracenote") or load_state(args.state_dir, "gracenote")

    explicit_mappings = _load_csv_mappings(args.mapping_csv) if args.mapping_csv else {}
    lookups = build_gracenote_lookups(gracenote_data)
    result = enrich_m3u_text(m3u_text, explicit_mappings, lookups)

    save_state(args.state_dir, "enriched_m3u", result.text, text=True)
    state["enriched_m3u"] = result.text
    for ch in result.skipped_channels:
        logger.warning(f"   ⚠️  No Gracenote match, channel excluded from M3U: {ch!r}")
    logger.info(f"✅  Step 7 done — enriched: {result.enriched}, skipped: {result.skipped}")


def step_8(args, state: dict) -> None:
    """Push enriched M3U to Channels DVR."""
    logger.info("▶  Step 8: Updating Channels DVR source")
    if not args.source_id:
        sys.exit("❌  --source-id is required for step 8")
    enriched_m3u = state.get("enriched_m3u") or load_state(args.state_dir, "enriched_m3u", text=True)
    client = state.setdefault("channels_dvr", _channels_dvr(args))
    client.update_m3u_source(
        source_id=args.source_id,
        display_name=args.source_display_name or args.source_id,
        m3u_text=enriched_m3u,
    )
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
        "--scan-poll-interval", type=int, default=5, metavar="SEC", help="How often to poll scan status (default: 5)"
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
            "name@frequency in MHz ('BBC One HD@10773,ITV HD@11386'). "
            "Omit to list available channels without adding any."
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
        "--mapping-csv",
        default=None,
        metavar="PATH",
        help="CSV with Name,Callsign overrides for channels that don't auto-match",
    )

    g = p.add_argument_group("Step 8 — Push to Channels DVR")
    g.add_argument("--source-id", default=None, metavar="ID", help="Channels DVR M3U source ID (required for step 8)")
    g.add_argument(
        "--source-display-name",
        default=None,
        metavar="NAME",
        help="Display name for the source (defaults to --source-id)",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()
    steps_to_run = sorted(args.steps) if args.steps else ALL_STEPS
    logger.info(f"Running steps: {steps_to_run}")
    state: dict = {}
    for n in steps_to_run:
        try:
            STEPS[n](args, state)
        except TimeoutError as exc:
            sys.exit(f"❌  Step {n} timed out: {exc}\n   Try increasing --scan-timeout")
        except requests.exceptions.ConnectionError as exc:
            sys.exit(f"❌  Step {n} connection error — is the host reachable?\n   {exc}")
        except requests.exceptions.HTTPError as exc:
            sys.exit(f"❌  Step {n} HTTP error: {exc}")
        except RuntimeError as exc:
            sys.exit(f"❌  Step {n} failed: {exc}")
    logger.info("🎉  Pipeline complete")


if __name__ == "__main__":
    main()
