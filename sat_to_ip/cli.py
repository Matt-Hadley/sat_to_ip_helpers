"""The sat-to-ip command.

  sat-to-ip run -c sat-to-ip.toml                     # refresh, select, publish
  sat-to-ip run -c sat-to-ip.toml --stages publish    # just republish what the receiver has
  sat-to-ip run -c sat-to-ip.toml --dry-run           # check everything, change nothing
  sat-to-ip channels -c sat-to-ip.toml                # tick channels from the receiver; saves [[channels]]
  sat-to-ip check-config -c sat-to-ip.toml            # validate the config without connecting

Exit codes (pipeline.ExitCode), so a scheduler can tell what happened:
  0 everything succeeded
  1 something failed: a device unreachable, an unexpected reply, a bug
  2 the config or the command line is wrong; nothing ran
  3 a safety check refused a change, so that part changed nothing (see the summary)
  4 it all ran, but something needs a person; listed under "need attention" in the summary
When several apply, the lowest non-zero code wins.
"""

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sat_to_ip import channel_setup, pipeline
from sat_to_ip.config import Config, load_config
from sat_to_ip.exceptions import ConfigError, PipelineError

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
    run.add_argument("--dry-run", action="store_true", help="check everything, change nothing")

    channels = sub.add_parser(
        "channels", help="pick channels from what the receiver has found; saves them in the config"
    )
    channels.add_argument("-c", "--config", type=Path, required=True, help="path to the TOML config")

    check = sub.add_parser("check-config", help="validate the config without connecting to anything")
    check.add_argument("-c", "--config", type=Path, required=True, help="path to the TOML config")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s"
    )
    if not args.verbose:  # its "Retrying (Retry(total=2…" lines alarm more than they help; failures are reported anyway
        logging.getLogger("urllib3").setLevel(logging.ERROR)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return pipeline.ExitCode.CONFIG

    if args.command == "channels":
        if not sys.stdin.isatty():
            logger.error("sat-to-ip channels needs a terminal to show the channel picker")
            return pipeline.ExitCode.CONFIG
        try:
            channel_setup.choose_channels(config)
        except ConfigError as exc:
            logger.error(str(exc))
            return pipeline.ExitCode.CONFIG
        except PipelineError as exc:
            logger.error(str(exc))
            return pipeline.ExitCode.FAILED
        except Exception as exc:  # e.g. a receiver raising something unexpected; never a traceback for users
            logger.debug("channels failed", exc_info=exc)
            logger.error(f"{type(exc).__name__}: {exc} (run with -v for details)")
            return pipeline.ExitCode.FAILED
        return pipeline.ExitCode.OK

    if args.command == "check-config":
        guides = ", ".join(f"{name} ({g.kind})" for name, g in config.guides.items()) or "none"
        print(f"Config OK: receiver {config.receiver.name}; guides: {guides}")
        print(f"Publishers: {', '.join(p.name for p in config.publishers)}")
        for note in _notes(config):
            print(f"Note: {note}")
        return pipeline.ExitCode.OK

    return _run(config, args)


def _run(config: Config, args: argparse.Namespace) -> int:
    options = pipeline.RunOptions(
        stages=frozenset(pipeline.Stage(s) for s in args.stages),
        allow_removals=args.allow_removals,
        dry_run=args.dry_run,
    )
    attention = _Attention()
    logging.getLogger().addHandler(attention)
    try:
        report = pipeline.run(config, options)
    finally:
        logging.getLogger().removeHandler(attention)
    logger.info("Summary:\n" + report.summary())
    if attention.messages:
        logger.error(
            f"{len(attention.messages)} thing(s) need attention; the run can't fix them by itself:\n  "
            + "\n  ".join(attention.messages)
        )
    return report.exit_code or (pipeline.ExitCode.ATTENTION if attention.messages else pipeline.ExitCode.OK)


class _Attention(logging.Handler):
    """Collects the warnings logged with checks.NEEDS_ATTENTION."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "needs_attention", False):
            self.messages.append(record.getMessage().strip())


def _notes(config: Config) -> list[str]:
    """Things that aren't errors but will bite on the first real run."""
    notes = []
    if config.channel_specs is None:
        notes.append(f"no [[channels]] yet; pick them with: sat-to-ip channels -c {config.path}")
    for pub in config.publishers:
        folder = getattr(pub, "output_dir", None)
        if not isinstance(folder, Path):
            continue
        existing = next((p for p in (folder, *folder.parents) if p.exists()), None)
        if existing is None or not os.access(existing, os.W_OK):
            notes.append(f"{pub.name}: can't create or write {folder}; fix output_dir or its permissions")
        elif existing != folder:
            notes.append(f"{pub.name}: {folder} doesn't exist yet; it'll be created on the first run")
    return notes


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
