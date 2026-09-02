"""Stage 5 — post-processing.

Merge normalized lead + evidence + decision into `LeadResult`, attach
diagnostics (LLM-vs-rubric disagreement, forced-review policy compliance),
compute the priority ranking, and roll up aggregate stats (pipeline spec §3.6,
rubric-gap B).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date

from .config import AppConfig
from .models import (
    LeadDecision,
    LeadResult,
    NormalizedLead,
    QUALIFIED,
    REJECTED,
    REVIEW,
    RubricEvidence,
    RunStats,
)

logger = logging.getLogger(__name__)

# Human-readable labels for the factor that most likely sank a rejected lead.
_FACTOR_LABELS = {
    "size": "company size outside target range",
    "industry": "industry not a fit",
    "source": "low-intent lead source",
    "recency": "interaction too old / no recent engagement",
}


def build_results(
    scored: list[tuple[NormalizedLead, RubricEvidence]],
    decisions: dict[str, LeadDecision],
    config: AppConfig,
) -> list[LeadResult]:
    results: list[LeadResult] = []
    for lead, ev in scored:
        decision = decisions[lead.lead_id]
        results.append(
            LeadResult(
                normalized=lead,
                evidence=ev,
                decision=decision,
                llm_rubric_disagreement=_disagrees(decision, ev),
                policy_flags=_policy_flags(decision, ev),
            )
        )
    return results


def priority_order(results: list[LeadResult]) -> list[str]:
    """lead_ids of QUALIFIED leads, best first (rubric-gap B).

    Sort by final_score desc, then most-recent interaction, then company A-Z so
    re-runs are byte-identical.
    """
    qualified = [r for r in results if r.decision.decision == QUALIFIED]
    qualified.sort(
        key=lambda r: (
            -r.evidence.final_score,
            -_date_ordinal(r.normalized.last_interaction_date),
            (r.normalized.company or "").lower(),
        )
    )
    return [r.lead_id for r in qualified]


def compute_stats(results: list[LeadResult]) -> RunStats:
    stats = RunStats(total_processed=len(results))
    rejection_factors: Counter[str] = Counter()
    dq_counter: Counter[str] = Counter()

    for r in results:
        decision = r.decision.decision
        if decision == QUALIFIED:
            stats.qualified_count += 1
        elif decision == REJECTED:
            stats.rejected_count += 1
            rejection_factors[_dominant_weak_factor(r.evidence)] += 1
        elif decision == REVIEW:
            stats.review_count += 1

        if r.decision.llm_call_failed:
            stats.llm_call_failures += 1
        if r.llm_rubric_disagreement:
            stats.llm_rubric_disagreements += 1
        if r.policy_flags:
            stats.policy_flag_count += 1
        for flag in r.all_data_quality_flags:
            dq_counter[flag] += 1

    stats.common_rejection_reasons = [
        (_FACTOR_LABELS.get(factor, factor), count)
        for factor, count in rejection_factors.most_common()
    ]
    stats.data_quality_flag_counts = dict(dq_counter.most_common())

    logger.info(
        "postprocess: %d qualified / %d review / %d rejected; "
        "%d LLM failures; %d LLM-vs-rubric disagreements; %d policy flags",
        stats.qualified_count,
        stats.review_count,
        stats.rejected_count,
        stats.llm_call_failures,
        stats.llm_rubric_disagreements,
        stats.policy_flag_count,
    )
    return stats


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def _disagrees(decision: LeadDecision, ev: RubricEvidence) -> bool:
    if decision.decision is None:  # call failed — nothing to compare
        return False
    return decision.decision != ev.rubric_band


def _policy_flags(decision: LeadDecision, ev: RubricEvidence) -> tuple[str, ...]:
    """Forced-review rule is an instruction to the LLM, not an override. If the
    LLM ignored it, surface it for QA rather than silently correcting."""
    flags: list[str] = []
    if (
        ev.forced_review_expected
        and decision.decision is not None
        and decision.decision != REVIEW
    ):
        flags.append("forced_review_not_applied")
    return tuple(flags)


def _dominant_weak_factor(ev: RubricEvidence) -> str:
    """Which factor contributed least — the headline reason for a rejection."""
    candidates = {
        "size": ev.size_score,
        "industry": ev.industry_score,
        "source": ev.source_score,
        "recency": ev.recency_multiplier * 10,  # rescale 0-1 -> 0-10 to compare
    }
    return min(candidates, key=candidates.get)  # type: ignore[arg-type]


def _date_ordinal(value: date | None) -> int:
    return value.toordinal() if value else 0
