"""Wires the six stages together (pipeline spec §2).

A failure in one stage should not silently corrupt the run: ingest/normalize/
rubric are deterministic and cheap; the LLM stage isolates failures per batch;
report writing always runs on whatever results exist.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, load_config
from .decision import decide_leads
from .ingest import ingest_files
from .llm import DecisionClient, GroqDecisionClient, MockDecisionClient
from .normalize import normalize_leads
from .postprocess import build_results, compute_stats, priority_order
from .rubric import evaluate
from .report import build_report, write_batch_log, write_csv, write_report

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    inputs: list[Path]
    config_path: Path
    force_offline: bool = False
    limit: int | None = None
    evaluation_date_override: str | None = None


def run(options: RunOptions) -> dict:
    config = load_config(options.config_path)
    if options.evaluation_date_override:
        config = _with_eval_date(config, options.evaluation_date_override)

    # -- Stage 1: ingest -----------------------------------------------------
    raw_leads = ingest_files(options.inputs)
    if options.limit is not None:
        raw_leads = raw_leads[: options.limit]
        logger.info("pipeline: limited to first %d leads", len(raw_leads))
    if not raw_leads:
        raise SystemExit("no leads to process — check the input path(s)")

    # -- Stage 2: normalize ------------------------------------------------- -
    normalized = normalize_leads(raw_leads, config)

    # -- Stage 3: rubric evidence (deterministic) --------------------------- -
    interaction_dates = [n.last_interaction_date for n in normalized if n.last_interaction_date]
    evaluation_date = config.resolve_evaluation_date(interaction_dates)
    logger.info("pipeline: scoring recency as of %s", evaluation_date.isoformat())
    scored = [(n, evaluate(n, evaluation_date, config)) for n in normalized]

    # -- Stage 4: LLM decisions (batched, failure-isolated) ---------------- -
    client = _make_client(config, force_offline=options.force_offline)
    decisions, batch_log = decide_leads(scored, client, config)

    # -- Stage 5: post-process ------------------------------------------------
    results = build_results(scored, decisions, config)
    priority_ids = priority_order(results)
    stats = compute_stats(results)

    # -- Stage 6: report ----------------------------------------------------
    report = build_report(
        results,
        priority_ids,
        stats,
        evaluation_date=evaluation_date,
        inputs=[p.name for p in options.inputs],
    )
    write_report(report, config.paths.output_report)
    write_csv(results, priority_ids, config.paths.output_csv)
    write_batch_log(batch_log, config.paths.batch_log)

    _log_headline(stats, config)
    return report


# --------------------------------------------------------------------------- #
def _make_client(config: AppConfig, *, force_offline: bool) -> DecisionClient:
    if force_offline or config.llm.offline:
        logger.info("pipeline: using offline MockDecisionClient (no API calls)")
        return MockDecisionClient()

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "GROQ_API_KEY not set — falling back to offline MockDecisionClient. "
            "Set the key or pass --offline to silence this."
        )
        return MockDecisionClient()

    if config.llm.provider != "groq":
        raise SystemExit(f"unsupported llm.provider: {config.llm.provider!r}")
    logger.info("pipeline: using GroqDecisionClient (model=%s)", config.llm.model)
    return GroqDecisionClient(config.llm, api_key=api_key)


def _with_eval_date(config: AppConfig, value: str) -> AppConfig:
    from dataclasses import replace

    return replace(config, evaluation_date_setting=value)


def _log_headline(stats, config: AppConfig) -> None:
    logger.info(
        "DONE: %d leads | %d qualified (%.1f%%) | %d review | %d rejected | "
        "%d LLM failures",
        stats.total_processed,
        stats.qualified_count,
        stats.qualified_pct,
        stats.review_count,
        stats.rejected_count,
        stats.llm_call_failures,
    )
