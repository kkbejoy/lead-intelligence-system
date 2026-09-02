"""Stage 2 — validate & normalize.

Turn each `RawLead`'s strings into typed values (int size, `date`), and record
*which* fields were missing or malformed. Detection only — no scoring here, and
no row is ever dropped (pipeline spec §3.2).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from .config import AppConfig
from .models import NormalizedLead, RawLead

logger = logging.getLogger(__name__)

# Accepts "50", "50-200", "50 to 200", "50+", "1,200", "~500", "500 employees".
_INT_IN_TEXT_RE = re.compile(r"-?\d[\d,]*")


def normalize_leads(raws: list[RawLead], config: AppConfig) -> list[NormalizedLead]:
    normalized = [_normalize_one(raw, config) for raw in raws]
    _log_summary(normalized)
    return normalized


def _normalize_one(raw: RawLead, config: AppConfig) -> NormalizedLead:
    dq_flags: list[str] = []
    pers_flags: list[str] = []

    name = _text_or_missing(raw.name, config)
    company = _text_or_missing(raw.company, config)
    if name is None:
        pers_flags.append("missing_name")
    if company is None:
        pers_flags.append("missing_company")

    industry = _text_or_missing(raw.industry, config)
    if industry is None:
        dq_flags.append("industry_missing")

    source = _text_or_missing(raw.source, config)
    if source is None:
        dq_flags.append("source_missing")

    company_size = _parse_company_size(raw.company_size, config)
    if company_size is None:
        dq_flags.append("company_size_missing")

    last_date, date_flag = _parse_date(raw.last_interaction_date, config)
    if date_flag:
        dq_flags.append(date_flag)

    return NormalizedLead(
        raw=raw,
        name=name,
        company=company,
        company_size=company_size,
        industry=industry,
        source=source,
        last_interaction_date=last_date,
        data_quality_flags=tuple(dq_flags),
        personalization_flags=tuple(pers_flags),
    )


# --------------------------------------------------------------------------- #
# Field parsers
# --------------------------------------------------------------------------- #
def _text_or_missing(value: str | None, config: AppConfig) -> str | None:
    """Return the trimmed string, or None if it reads as a missing marker.

    A value is missing if it is empty, is a missing token, or *contains* a
    missing token as one of its words ("Unknown Sector" -> missing), which is
    how the sample data encodes unmapped categories.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    words = {w.strip().lower() for w in re.split(r"[\s/]+", trimmed)}
    if trimmed.lower() in config.missing_tokens or words & config.missing_tokens:
        return None
    return trimmed


def _parse_company_size(value: str | None, config: AppConfig) -> int | None:
    """Best-effort integer employee count.

    Handles plain ints and common range/label forms; the *lower* bound of a
    range is used. Non-positive or unparseable -> missing (spec §4, Gap C).
    """
    if _text_or_missing(value, config) is None:
        return None
    match = _INT_IN_TEXT_RE.search(value)  # type: ignore[arg-type]
    if not match:
        logger.debug("company_size %r not numeric -> missing", value)
        return None
    number = int(match.group().replace(",", ""))
    if number <= 0:
        logger.debug("company_size %r <= 0 -> missing", value)
        return None
    return number


def _parse_date(value: str | None, config: AppConfig) -> tuple[date | None, str | None]:
    """Parse an ISO date. Returns (date, flag) where flag is set on a problem.

    A future date is kept (recency scoring clamps it) but flagged; anything
    unparseable is treated as missing.
    """
    if _text_or_missing(value, config) is None:
        return None, "date_missing"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).date()  # type: ignore[union-attr]
            return parsed, None
        except ValueError:
            continue
    logger.debug("last_interaction_date %r unparseable -> treated as missing", value)
    return None, "date_invalid"


# --------------------------------------------------------------------------- #
def _log_summary(leads: list[NormalizedLead]) -> None:
    flagged = [ld for ld in leads if ld.data_quality_flags or ld.personalization_flags]
    counts: dict[str, int] = {}
    for lead in leads:
        for flag in (*lead.data_quality_flags, *lead.personalization_flags):
            counts[flag] = counts.get(flag, 0) + 1
    logger.info(
        "normalize: %d leads, %d with >=1 flag; breakdown=%s",
        len(leads),
        len(flagged),
        counts or "{}",
    )
