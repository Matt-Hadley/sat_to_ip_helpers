"""The sat-to-ip command.

  sat-to-ip run -c sat-to-ip.toml                     # refresh, select, publish
  sat-to-ip run -c sat-to-ip.toml --stages publish    # just republish what the receiver has
  sat-to-ip run -c sat-to-ip.toml --dry-run           # check everything, change nothing
  sat-to-ip check-config -c sat-to-ip.toml            # validate the config without connecting

Exit codes: 0 everything succeeded, 1 something failed (see the summary), 2 bad config or usage.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sat_to_ip import pipeline
from sat_to_ip.config import load_config
from sat_to_ip.exceptions import ConfigError

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sat-to-ip", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline")
    run.add_argument("-c", "--config", type=Path, required=True, help="path to the TOML config")
    run.add_argument(
        "--stages",
        nargs="+",
        choices=[s.value for s in pipeline.Stage],
        default=[s.value for s in pipeline.Stage],
        help="stages to run (default: all)",
    )
    run.add_argument("--allow-removals", action="store_true", help="skip the [checks] max_removed check")
    run.add_argument("--interactive", action="store_true", help="pick channels in the terminal instead of the file")
    run.add_argument("--dry-run", action="store_true", help="check everything, change nothing")

    check = sub.add_parser("check-config", help="validate the config without connecting to anything")
    check.add_argument("-c", "--config", type=Path, required=True, help="path to the TOML config")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s"
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error(f"Config error: {exc}")
        return 2

    if args.command == "check-config":
        guides = ", ".join(f"{name} ({g.kind})" for name, g in config.guides.items()) or "none"
        print(f"Config OK: receiver {config.receiver.name}; guides: {guides}")
        print(f"Publishers: {', '.join(p.name for p in config.publishers)}")
        return 0

    options = pipeline.RunOptions(
        stages=frozenset(pipeline.Stage(s) for s in args.stages),
        allow_removals=args.allow_removals,
        interactive=args.interactive,
        dry_run=args.dry_run,
    )
    report = pipeline.run(config, options)
    logger.info("Summary:\n" + report.summary())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
