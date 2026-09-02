"""Stage 3 (rubric) — the worked examples from
lead_qualification_rubric_spec.md §7, plus the forced-review logic.
"""

from __future__ import annotations

from datetime import date

import pytest

from lead_intel.models import QUALIFIED, REJECTED, REVIEW
from lead_intel.normalize import normalize_leads
from lead_intel.rubric import evaluate

EVAL_DATE = date(2024, 1, 20)  # matches "0 days ago" in the spec examples


def _evidence(make_raw, config, **overrides):
    lead = normalize_leads([make_raw(**overrides)], config)[0]
    return lead, evaluate(lead, EVAL_DATE, config)


def test_example_a_clean_strong_lead(make_raw, config):
    _, ev = _evidence(
        make_raw, config,
        company_size="50", industry="SaaS",
        source="Inbound demo request", last_interaction_date="2024-01-20",
    )
    assert ev.size_score == 10
    assert ev.industry_score == 10
    assert ev.fit_score == 10
    assert ev.intent_score == 10
    assert ev.final_score == 10.0
    assert ev.rubric_band == QUALIFIED
    assert ev.forced_review_expected is False


def test_example_b_clean_poor_lead(make_raw, config):
    _, ev = _evidence(
        make_raw, config,
        company_size="5000", industry="Finance",
        source="Content download", last_interaction_date="2023-12-01",  # ~50 days
    )
    assert ev.size_score == 3
    assert ev.industry_score == 3
    assert ev.recency_multiplier == 0.75
    assert ev.final_score == pytest.approx(3.0, abs=0.01)
    assert ev.rubric_band == REJECTED


def test_example_c_missing_size_forces_review_despite_high_score(make_raw, config):
    lead, ev = _evidence(
        make_raw, config,
        company_size="NA", industry="SaaS",
        source="Inbound demo request", last_interaction_date="2024-01-20",
    )
    assert ev.size_score == 5  # neutral default, not worst-case
    assert ev.final_score == pytest.approx(8.08, abs=0.01)
    assert ev.rubric_band == QUALIFIED          # score alone would qualify...
    assert ev.forced_review_expected is True    # ...but policy says review


def test_example_d_known_bad_fit_is_not_forced_review(make_raw, config):
    lead, ev = _evidence(
        make_raw, config,
        company_size="50000", industry="Enterprise Software",
        source="Inbound demo request", last_interaction_date="2024-01-20",
    )
    assert ev.size_score == 1
    assert ev.industry_score == 6            # real-but-unmapped -> neutral
    assert ev.final_score == pytest.approx(5.88, abs=0.01)
    assert ev.rubric_band == REVIEW          # by score, not by override
    assert ev.forced_review_expected is False
    assert lead.data_quality_flags == ()


def test_example_e_everything_missing_forces_review(make_raw, config):
    _, ev = _evidence(
        make_raw, config,
        company_size="NA", industry="NA", source="NA", last_interaction_date="NA",
    )
    assert ev.recency_multiplier == 0.5
    assert ev.forced_review_expected is True


def test_future_date_is_clamped_and_flagged(make_raw, config):
    _, ev = _evidence(make_raw, config, last_interaction_date="2099-01-01")
    assert ev.recency_multiplier == 1.0
    assert "date_future" in ev.extra_data_quality_flags


def test_two_lighter_fields_missing_forces_review(make_raw, config):
    _, ev = _evidence(
        make_raw, config,
        company_size="100", industry="NA", source="NA",
        last_interaction_date="2024-01-20",
    )
    assert ev.forced_review_expected is True


def test_one_lighter_field_missing_does_not_force_review(make_raw, config):
    _, ev = _evidence(
        make_raw, config,
        company_size="100", industry="NA",
        source="Referral", last_interaction_date="2024-01-20",
    )
    assert ev.forced_review_expected is False
