"""Immutable data structures passed between pipeline stages.

Each stage takes the previous stage's object and returns a richer one; nothing
is mutated in place, which keeps stages independently testable and makes a
partial failure easy to reason about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Canonical decision vocabulary. The LLM must return one of these.
QUALIFIED = "qualified"
REJECTED = "rejected"
REVIEW = "review"
DECISIONS = (QUALIFIED, REJECTED, REVIEW)

EXPECTED_COLUMNS = (
    "name",
    "company",
    "company_size",
    "industry",
    "source",
    "last_interaction_date",
)


@dataclass(frozen=True)
class RawLead:
    """One CSV row, exactly as read — every value is a string or None."""

    lead_id: str
    source_file: str
    row_number: int  # 1-based line number in the source file (for tracing)
    name: str | None
    company: str | None
    company_size: str | None
    industry: str | None
    source: str | None
    last_interaction_date: str | None

    def raw_fields(self) -> dict[str, str | None]:
        """The six business fields as a plain dict (for the report)."""
        return {col: getattr(self, col) for col in EXPECTED_COLUMNS}


@dataclass(frozen=True)
class NormalizedLead:
    """A RawLead after type coercion and missing-data detection."""

    raw: RawLead
    name: str | None
    company: str | None
    company_size: int | None
    industry: str | None
    source: str | None
    last_interaction_date: date | None
    # Which scored fields were missing/defaulted (rubric spec §4).
    data_quality_flags: tuple[str, ...] = ()
    # Missing name/company — affects personalisation, not the decision (§4.1).
    personalization_flags: tuple[str, ...] = ()

    @property
    def lead_id(self) -> str:
        return self.raw.lead_id


@dataclass(frozen=True)
class RubricEvidence:
    """Deterministic structured evidence handed to the LLM. Not a verdict."""

    size_score: float
    industry_score: float
    source_score: float
    recency_multiplier: float
    fit_score: float
    intent_score: float
    final_score: float
    rubric_band: str  # qualified/rejected/review implied by final_score alone
    # Forced-review expectation from spec §4.1 (QA signal, not an override).
    forced_review_expected: bool
    # Extra flags discovered while scoring (e.g. a future-dated interaction).
    extra_data_quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeadDecision:
    """The LLM's verdict for one lead (or a fallback when the call failed)."""

    decision: str | None  # one of DECISIONS, or None if the call failed
    reasoning: str
    outreach_message: str | None
    llm_call_failed: bool = False


@dataclass(frozen=True)
class LeadResult:
    """Everything known about one lead after the full pipeline."""

    normalized: NormalizedLead
    evidence: RubricEvidence
    decision: LeadDecision
    # Post-processing diagnostics (pipeline spec §3.6).
    llm_rubric_disagreement: bool = False
    policy_flags: tuple[str, ...] = ()

    @property
    def lead_id(self) -> str:
        return self.normalized.lead_id

    @property
    def all_data_quality_flags(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(  # de-dupe, preserve order
                (
                    *self.normalized.data_quality_flags,
                    *self.evidence.extra_data_quality_flags,
                )
            )
        )


@dataclass
class BatchLogRecord:
    """One line in the structured batch log (pipeline spec §3.5)."""

    batch_index: int
    lead_ids: list[str]
    status: str  # "ok" | "failed"
    attempts: int
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class RunStats:
    """Aggregate figures for the report's run_summary block (§3.6)."""

    total_processed: int = 0
    qualified_count: int = 0
    review_count: int = 0
    rejected_count: int = 0
    llm_call_failures: int = 0
    llm_rubric_disagreements: int = 0
    policy_flag_count: int = 0
    common_rejection_reasons: list[tuple[str, int]] = field(default_factory=list)
    data_quality_flag_counts: dict[str, int] = field(default_factory=dict)

    @property
    def qualified_pct(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return round(100 * self.qualified_count / self.total_processed, 1)
