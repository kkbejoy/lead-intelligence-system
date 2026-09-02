"""Stage 2 (normalize) — missing-data detection and type coercion."""

from __future__ import annotations

from datetime import date

from lead_intel.normalize import normalize_leads


def _one(make_raw, config, **overrides):
    return normalize_leads([make_raw(**overrides)], config)[0]


def test_clean_lead_has_no_flags(make_raw, config):
    lead = _one(make_raw, config)
    assert lead.company_size == 100
    assert lead.industry == "SaaS"
    assert lead.last_interaction_date == date(2024, 1, 20)
    assert lead.data_quality_flags == ()
    assert lead.personalization_flags == ()


def test_blank_and_na_company_size_flagged(make_raw, config):
    for value in ("", "   ", "NA", "n/a", "unknown"):
        lead = _one(make_raw, config, company_size=value)
        assert lead.company_size is None
        assert "company_size_missing" in lead.data_quality_flags


def test_unknown_sector_is_missing_not_unrecognised(make_raw, config):
    # "Unknown Sector" contains the token 'unknown' -> treated as missing.
    lead = _one(make_raw, config, industry="Unknown Sector")
    assert lead.industry is None
    assert "industry_missing" in lead.data_quality_flags


def test_real_but_unmapped_industry_is_kept(make_raw, config):
    # "Enterprise Software" is a real value we just haven't tiered -> no flag.
    lead = _one(make_raw, config, industry="Enterprise Software")
    assert lead.industry == "Enterprise Software"
    assert "industry_missing" not in lead.data_quality_flags


def test_unknown_source_is_missing(make_raw, config):
    lead = _one(make_raw, config, source="Unknown Source")
    assert lead.source is None
    assert "source_missing" in lead.data_quality_flags


def test_company_size_range_string_takes_lower_bound(make_raw, config):
    assert _one(make_raw, config, company_size="50-200").company_size == 50
    assert _one(make_raw, config, company_size="1,200").company_size == 1200
    assert _one(make_raw, config, company_size="~500 employees").company_size == 500


def test_non_positive_or_garbage_company_size_is_missing(make_raw, config):
    for value in ("0", "-5", "SMB", "large"):
        lead = _one(make_raw, config, company_size=value)
        assert lead.company_size is None
        assert "company_size_missing" in lead.data_quality_flags


def test_unparseable_date_flagged_invalid(make_raw, config):
    lead = _one(make_raw, config, last_interaction_date="13/45/2020")
    assert lead.last_interaction_date is None
    assert "date_invalid" in lead.data_quality_flags


def test_missing_date_flagged_missing(make_raw, config):
    lead = _one(make_raw, config, last_interaction_date="")
    assert lead.last_interaction_date is None
    assert "date_missing" in lead.data_quality_flags


def test_missing_name_and_company_are_personalization_flags(make_raw, config):
    lead = _one(make_raw, config, name="", company="")
    assert "missing_name" in lead.personalization_flags
    assert "missing_company" in lead.personalization_flags
    # personalization problems must not leak into data-quality flags
    assert lead.data_quality_flags == ()


def test_all_fields_missing_row_survives(make_raw, config):
    lead = _one(
        make_raw, config,
        name="x", company="x",
        company_size="NA", industry="NA", source="NA", last_interaction_date="NA",
    )
    assert {"company_size_missing", "industry_missing", "source_missing", "date_missing"} <= set(
        lead.data_quality_flags
    )
