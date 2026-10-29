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
    CHANNELS_DVR_SID   (optional: only if Channels DVR asks for a login)

Examples:
  # Run the full pipeline:
  sat-to-ip --octopus-host octopus.local --source-id MySource

  # Trigger an Octopus scan only (step 3):
  sat-to-ip --steps 3 --octopus-host octopus.local --positions 28.2E

  # Re-run enrichment and push using the built-in UK mappings (steps 7-8):
  sat-to-ip --steps 7 8 --source-id MySource --mapping-region uk
"""

import argparse
import logging
import sys

import requests
import urllib3

from sat_to_ip.exceptions import PipelineError
from sat_to_ip.kingofsat import DEFAULT_BASE_URL
from sat_to_ip.m3u.mappings import available_regions
from sat_to_ip.steps import ALL_STEPS, STEPS

logger = logging.getLogger(__name__)

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
    g.add_argument("--kos-base-url", default=DEFAULT_BASE_URL, metavar="URL", help=argparse.SUPPRESS)

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

    g.add_argument(
        "--max-removed",
        type=int,
        default=5,
        metavar="N",
        help="With --add-channels, fail rather than remove more than N channels already in the DMS (default: 5)",
    )
    g.add_argument(
        "--allow-removals",
        action="store_true",
        help="Skip the --max-removed check, e.g. after deliberately trimming the channel list",
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
        "--channels-dvr-sid",
        default=None,
        metavar="SID",
        help="SID session cookie, if Channels DVR requires a login. Overrides CHANNELS_DVR_SID env var",
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

    g.add_argument(
        "--min-guide-match",
        type=float,
        default=0.5,
        metavar="RATIO",
        help="Fail if fewer than this share of channels match Gracenote (default: 0.5)",
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
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
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
