"""Stage 3 — deterministic rubric evidence.

Pure functions, no I/O, no LLM. Implements every table and formula in
`lead_qualification_rubric_spec.md` §3-§6. The output is *evidence* the LLM
reasons over; `rubric_band` and `forced_review_expected` are diagnostics only
(pipeline spec §1).
"""

from __future__ import annotations

from datetime import date

from .config import AppConfig, RubricConfig
from .models import (
    NormalizedLead,
    QUALIFIED,
    REJECTED,
    REVIEW,
    RubricEvidence,
)

_LIGHTER_FIELDS = ("industry_missing", "source_missing", "date_missing", "date_invalid")


def evaluate(lead: NormalizedLead, evaluation_date: date, config: AppConfig) -> RubricEvidence:
    rc = config.rubric
    extra_flags: list[str] = []

    size_score = _size_score(lead.company_size, rc)
    industry_score = _industry_score(lead.industry, rc)
    source_score = _source_score(lead.source, rc)
    recency_multiplier = _recency_multiplier(
        lead.last_interaction_date, evaluation_date, rc, extra_flags
    )

    fit_score = (
        rc.weights["fit_size"] * size_score
        + rc.weights["fit_industry"] * industry_score
    )
    intent_score = source_score * recency_multiplier
    final_score = (
        rc.weights["final_fit"] * fit_score
        + rc.weights["final_intent"] * intent_score
    )

    return RubricEvidence(
        size_score=round(size_score, 2),
        industry_score=round(industry_score, 2),
        source_score=round(source_score, 2),
        recency_multiplier=round(recency_multiplier, 3),
        fit_score=round(fit_score, 2),
        intent_score=round(intent_score, 2),
        final_score=round(final_score, 2),
        rubric_band=_band(final_score, rc),
        forced_review_expected=_forced_review_expected(lead, rc),
        extra_data_quality_flags=tuple(extra_flags),
    )


# --------------------------------------------------------------------------- #
# Factor scores
# --------------------------------------------------------------------------- #
def _size_score(size: int | None, rc: RubricConfig) -> float:
    if size is None:
        return rc.missing_defaults["company_size"]
    for band in rc.size_bands:
        if band["max"] is None or size <= band["max"]:
            return float(band["score"])
    return float(rc.size_bands[-1]["score"])  # unreachable; defensive


def _industry_score(industry: str | None, rc: RubricConfig) -> float:
    if industry is None:
        return rc.missing_defaults["industry"]
    key = industry.strip().lower()
    for tier in ("strong", "neutral", "weak"):
        if key in {v.lower() for v in rc.industry_tiers[tier]["values"]}:
            return float(rc.industry_tiers[tier]["score"])
    return float(rc.industry_tiers["unlisted_score"])  # real but unmapped value


def _source_score(source: str | None, rc: RubricConfig) -> float:
    if source is None:
        return rc.missing_defaults["source"]
    key = source.strip().lower()
    for name, score in rc.source_scores.items():
        if name == "unlisted_score":
            continue
        if key == name.lower():
            return float(score)
    return float(rc.source_scores["unlisted_score"])


def _recency_multiplier(
    last_date: date | None,
    evaluation_date: date,
    rc: RubricConfig,
    extra_flags: list[str],
) -> float:
    if last_date is None:
        return rc.missing_defaults["recency_multiplier"]
    days = (evaluation_date - last_date).days
    if days < 0:
        # Future-dated interaction: treat as "today" but flag the anomaly.
        extra_flags.append("date_future")
        days = 0
    for band in rc.recency_bands:
        if band["max_days"] is None or days <= band["max_days"]:
            return float(band["multiplier"])
    return float(rc.recency_bands[-1]["multiplier"])  # defensive


# --------------------------------------------------------------------------- #
# Diagnostics (never override the LLM)
# --------------------------------------------------------------------------- #
def _band(final_score: float, rc: RubricConfig) -> str:
    if final_score >= rc.qualified_min:
        return QUALIFIED
    if final_score < rc.rejected_below:
        return REJECTED
    return REVIEW


def _forced_review_expected(lead: NormalizedLead, rc: RubricConfig) -> bool:
    fr = rc.forced_review
    flags = set(lead.data_quality_flags)
    if fr.get("on_company_size_missing") and "company_size_missing" in flags:
        return True
    threshold = int(fr.get("lighter_fields_missing_threshold", 2))
    lighter_missing = sum(1 for f in _LIGHTER_FIELDS if f in flags)
    return lighter_missing >= threshold
