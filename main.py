"""Lead Intelligence System — command-line entry point.

Examples
--------
    # Full run on both sample files, using your Groq key from .env
    python main.py

    # Quick smoke test: first 8 leads, no API calls
    python main.py --offline --limit 8

    # Reproducible report pinned to a fixed scoring date
    python main.py --evaluation-date 2024-01-21
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from lead_intel.config import ConfigError, load_config
from lead_intel.pipeline import RunOptions, run
from lead_intel.utils import configure_logging

DEFAULT_INPUTS = ["leads_training.csv", "leads_testing.csv"]

logger = logging.getLogger("lead_intel.cli")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lead-intel",
        description="Auto-qualify inbound leads, sequence outreach, log insights.",
    )
    parser.add_argument(
        "-i", "--input", dest="inputs", action="append", metavar="CSV",
        help=f"lead CSV to process; repeatable (default: {', '.join(DEFAULT_INPUTS)})",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", type=Path,
        help="path to config.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="use the deterministic mock LLM (no network, no API key needed)",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="process only the first N leads (for quick tests)",
    )
    parser.add_argument(
        "--evaluation-date", metavar="AUTO|TODAY|YYYY-MM-DD",
        help="override config's recency reference date",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()  # pull GROQ_API_KEY from .env if present

    # Read config once up front just to learn where the run log should go.
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.paths.run_log, verbose=args.verbose)

    inputs = [Path(p) for p in (args.inputs or DEFAULT_INPUTS)]
    missing = [str(p) for p in inputs if not p.is_file()]
    if missing:
        logger.error("input file(s) not found: %s", ", ".join(missing))
        return 2

    options = RunOptions(
        inputs=inputs,
        config_path=args.config,
        force_offline=args.offline,
        limit=args.limit,
        evaluation_date_override=args.evaluation_date,
    )

    try:
        run(options)
    except SystemExit as exc:  # raised by the pipeline for user-facing problems
        logger.error("%s", exc)
        return 1
    except Exception:  # last-resort guard so we always exit cleanly with a log
        logger.exception("unexpected failure — see traceback above")
        return 1

    print(f"\nReport written to {config.paths.output_report}")
    print(f"Spreadsheet copy  {config.paths.output_csv}")
    print(f"Run log           {config.paths.run_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
