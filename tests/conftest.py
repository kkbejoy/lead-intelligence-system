"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from lead_intel.config import AppConfig, load_config
from lead_intel.models import RawLead

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


@pytest.fixture(scope="session")
def config() -> AppConfig:
    """The real config.yaml — the rubric numbers under test live there."""
    return load_config(_CONFIG_PATH)


@pytest.fixture
def make_raw():
    """Factory for a RawLead with sensible defaults; override per test."""

    def _factory(
        *,
        name: str | None = "Test Person",
        company: str | None = "Test Co",
        company_size: str | None = "100",
        industry: str | None = "SaaS",
        source: str | None = "Inbound demo request",
        last_interaction_date: str | None = "2024-01-20",
    ) -> RawLead:
        return RawLead(
            lead_id="L0001",
            source_file="unit-test",
            row_number=2,
            name=name,
            company=company,
            company_size=company_size,
            industry=industry,
            source=source,
            last_interaction_date=last_interaction_date,
        )

    return _factory
