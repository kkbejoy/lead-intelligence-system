"""Stage 4 — batch LLM decisions.

Builds the per-lead payload (raw fields + rubric evidence), splits leads into
batches, calls the LLM client once per batch, and isolates failures: a batch
that fails after retries yields fallback decisions for its leads and the run
continues (pipeline spec §3.5).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import AppConfig
from .llm import DecisionClient, LLMBatchError
from .models import (
    BatchLogRecord,
    LeadDecision,
    NormalizedLead,
    RubricEvidence,
)
from .utils import chunked

logger = logging.getLogger(__name__)

_FALLBACK_REASON = "LLM generation failed after retries — rubric evidence only"


def decide_leads(
    scored: list[tuple[NormalizedLead, RubricEvidence]],
    client: DecisionClient,
    config: AppConfig,
) -> tuple[dict[str, LeadDecision], list[BatchLogRecord]]:
    """Return {lead_id: LeadDecision} plus a batch-level log."""
    decisions: dict[str, LeadDecision] = {}
    batch_log: list[BatchLogRecord] = []

    batches = list(chunked(scored, config.llm.batch_size))
    logger.info(
        "decision: %d leads in %d batch(es) of up to %d",
        len(scored),
        len(batches),
        config.llm.batch_size,
    )

    for index, batch in enumerate(batches):
        payload = [_build_payload(lead, ev) for lead, ev in batch]
        lead_ids = [p["lead_id"] for p in payload]
        started = time.monotonic()
        try:
            results = client.decide_batch(payload)
            for result in results:
                decisions[result["lead_id"]] = LeadDecision(
                    decision=result["decision"],
                    reasoning=result["reasoning"],
                    outreach_message=result["outreach_message"] or None,
                )
            batch_log.append(
                BatchLogRecord(
                    batch_index=index,
                    lead_ids=lead_ids,
                    status="ok",
                    attempts=1,  # client retries internally; success is reported once
                    duration_seconds=round(time.monotonic() - started, 2),
                )
            )
            logger.info("decision: batch %d/%d ok (%d leads)", index + 1, len(batches), len(results))
        except LLMBatchError as exc:
            for lead_id in lead_ids:
                decisions[lead_id] = LeadDecision(
                    decision=None,
                    reasoning=_FALLBACK_REASON,
                    outreach_message=None,
                    llm_call_failed=True,
                )
            batch_log.append(
                BatchLogRecord(
                    batch_index=index,
                    lead_ids=lead_ids,
                    status="failed",
                    attempts=config.llm.max_retries,
                    error=str(exc),
                    duration_seconds=round(time.monotonic() - started, 2),
                )
            )
            logger.error(
                "decision: batch %d/%d failed, %d leads marked for manual handling: %s",
                index + 1,
                len(batches),
                len(lead_ids),
                exc,
            )

    return decisions, batch_log


def _build_payload(lead: NormalizedLead, ev: RubricEvidence) -> dict[str, Any]:
    """Exactly what the LLM sees for one lead: raw fields + evidence."""
    all_dq_flags = tuple(
        dict.fromkeys((*lead.data_quality_flags, *ev.extra_data_quality_flags))
    )
    return {
        "lead_id": lead.lead_id,
        "fields": {
            "name": lead.name,
            "company": lead.company,
            "company_size": lead.company_size,
            "industry": lead.industry,
            "source": lead.source,
            "last_interaction_date": (
                lead.last_interaction_date.isoformat()
                if lead.last_interaction_date
                else None
            ),
        },
        "evidence": {
            "size_score": ev.size_score,
            "industry_score": ev.industry_score,
            "source_score": ev.source_score,
            "recency_multiplier": ev.recency_multiplier,
            "fit_score": ev.fit_score,
            "intent_score": ev.intent_score,
            "final_score": ev.final_score,
            "rubric_band": ev.rubric_band,
            "data_quality_flags": list(all_dq_flags),
        },
    }
