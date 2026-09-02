"""Stage 6 — report assembly & writing.

Primary artefact: `output_report.json` (schema per pipeline spec §3.7).
Secondary: a flat CSV for spreadsheet users, and a JSON-lines batch log kept
separate from the report so the report stays clean for the sales team.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from .models import BatchLogRecord, LeadResult, RunStats

logger = logging.getLogger(__name__)

# How many qualified messages to surface inline as "samples" in the report.
_SAMPLE_MESSAGE_COUNT = 5


def build_report(
    results: list[LeadResult],
    priority_ids: list[str],
    stats: RunStats,
    *,
    evaluation_date: date,
    inputs: list[str],
) -> dict[str, Any]:
    priority_rank = {lead_id: rank for rank, lead_id in enumerate(priority_ids, start=1)}

    return {
        "run_summary": {
            "inputs": inputs,
            "evaluation_date": evaluation_date.isoformat(),
            "total_processed": stats.total_processed,
            "qualified_count": stats.qualified_count,
            "review_count": stats.review_count,
            "rejected_count": stats.rejected_count,
            "qualified_pct": stats.qualified_pct,
            "common_rejection_reasons": [
                {"reason": reason, "count": count}
                for reason, count in stats.common_rejection_reasons
            ],
            "data_quality_flag_counts": stats.data_quality_flag_counts,
            "llm_call_failures": stats.llm_call_failures,
            "llm_rubric_disagreements": stats.llm_rubric_disagreements,
            "policy_flag_count": stats.policy_flag_count,
        },
        "priority_queue": [
            _lead_brief(_find(results, lead_id), priority_rank[lead_id])
            for lead_id in priority_ids
        ],
        "review_queue": [
            _lead_brief(r, None)
            for r in _sorted_by_score(results, "review")
        ],
        "disqualified": [
            _lead_brief(r, None)
            for r in _sorted_by_score(results, "rejected")
        ],
        "sample_messages": _sample_messages(results, priority_ids),
        "leads": [_lead_full(r, priority_rank.get(r.lead_id)) for r in results],
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("report: wrote %s", path)


def write_csv(results: list[LeadResult], priority_ids: list[str], path: Path) -> None:
    priority_rank = {lead_id: rank for rank, lead_id in enumerate(priority_ids, start=1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "lead_id", "name", "company", "company_size", "industry", "source",
        "last_interaction_date", "final_score", "rubric_band", "decision",
        "priority_rank", "llm_rubric_disagreement", "llm_call_failed",
        "data_quality_flags", "personalization_flags", "policy_flags", "reasoning",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for r in results:
            n, ev, d = r.normalized, r.evidence, r.decision
            writer.writerow(
                {
                    "lead_id": r.lead_id,
                    "name": n.name or "",
                    "company": n.company or "",
                    "company_size": n.company_size if n.company_size is not None else "",
                    "industry": n.industry or "",
                    "source": n.source or "",
                    "last_interaction_date": (
                        n.last_interaction_date.isoformat()
                        if n.last_interaction_date
                        else ""
                    ),
                    "final_score": ev.final_score,
                    "rubric_band": ev.rubric_band,
                    "decision": d.decision or "",
                    "priority_rank": priority_rank.get(r.lead_id, ""),
                    "llm_rubric_disagreement": r.llm_rubric_disagreement,
                    "llm_call_failed": d.llm_call_failed,
                    "data_quality_flags": ";".join(r.all_data_quality_flags),
                    "personalization_flags": ";".join(n.personalization_flags),
                    "policy_flags": ";".join(r.policy_flags),
                    "reasoning": d.reasoning,
                }
            )
    logger.info("report: wrote %s", path)


def write_batch_log(records: list[BatchLogRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    logger.info("report: wrote %s (%d batch records)", path, len(records))


# --------------------------------------------------------------------------- #
# Shapers
# --------------------------------------------------------------------------- #
def _lead_brief(r: LeadResult, rank: int | None) -> dict[str, Any]:
    n, ev = r.normalized, r.evidence
    brief = {
        "lead_id": r.lead_id,
        "name": n.name,
        "company": n.company,
        "final_score": ev.final_score,
        "decision": r.decision.decision,
        "reasoning": r.decision.reasoning,
    }
    if rank is not None:
        brief["priority_rank"] = rank
    return brief


def _lead_full(r: LeadResult, rank: int | None) -> dict[str, Any]:
    n, ev, d = r.normalized, r.evidence, r.decision
    return {
        "lead_id": r.lead_id,
        "name": n.name,
        "company": n.company,
        "raw_fields": n.raw.raw_fields(),
        "source_file": n.raw.source_file,
        "row_number": n.raw.row_number,
        "rubric_evidence": {
            "size_score": ev.size_score,
            "industry_score": ev.industry_score,
            "source_score": ev.source_score,
            "recency_multiplier": ev.recency_multiplier,
            "fit_score": ev.fit_score,
            "intent_score": ev.intent_score,
            "final_score": ev.final_score,
            "rubric_band": ev.rubric_band,
            "forced_review_expected": ev.forced_review_expected,
        },
        "data_quality_flags": list(r.all_data_quality_flags),
        "personalization_flags": list(n.personalization_flags),
        "decision": d.decision,
        "priority_rank": rank,
        "reasoning": d.reasoning,
        "outreach_message": d.outreach_message or "",
        "llm_rubric_disagreement": r.llm_rubric_disagreement,
        "llm_call_failed": d.llm_call_failed,
        "policy_flags": list(r.policy_flags),
    }


def _sample_messages(results: list[LeadResult], priority_ids: list[str]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for lead_id in priority_ids[:_SAMPLE_MESSAGE_COUNT]:
        r = _find(results, lead_id)
        if r.decision.outreach_message:
            samples.append(
                {
                    "lead_id": r.lead_id,
                    "company": r.normalized.company,
                    "industry": r.normalized.industry,
                    "message": r.decision.outreach_message,
                }
            )
    return samples


def _sorted_by_score(results: list[LeadResult], decision: str) -> list[LeadResult]:
    subset = [r for r in results if r.decision.decision == decision]
    subset.sort(key=lambda r: -r.evidence.final_score)
    return subset


def _find(results: list[LeadResult], lead_id: str) -> LeadResult:
    for r in results:
        if r.lead_id == lead_id:
            return r
    raise KeyError(lead_id)  # pragma: no cover - ids come from `results`
