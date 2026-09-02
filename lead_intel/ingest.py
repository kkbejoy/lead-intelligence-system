"""Stage 1 — CSV ingest.

Read one or more lead CSVs into `RawLead` objects. Tolerates missing columns,
extra columns, blank lines and empty files without raising (pipeline spec
§3.1); a genuinely unreadable path is the caller's problem and propagates.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .models import EXPECTED_COLUMNS, RawLead

logger = logging.getLogger(__name__)


def ingest_files(paths: list[Path]) -> list[RawLead]:
    """Read every path in order, concatenating rows. IDs are globally unique."""
    leads: list[RawLead] = []
    for path in paths:
        leads.extend(_ingest_one(path, id_offset=len(leads)))
    logger.info("ingest: %d leads from %d file(s)", len(leads), len(paths))
    return leads


def _ingest_one(path: Path, *, id_offset: int) -> list[RawLead]:
    if not path.is_file():
        raise FileNotFoundError(f"input CSV not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing_cols = [c for c in EXPECTED_COLUMNS if c not in header]
        if missing_cols:
            logger.warning(
                "%s: missing column(s) %s — treated as empty for every row",
                path.name,
                ", ".join(missing_cols),
            )
        extra_cols = [c for c in header if c not in EXPECTED_COLUMNS]
        if extra_cols:
            logger.info("%s: ignoring extra column(s) %s", path.name, ", ".join(extra_cols))

        rows: list[RawLead] = []
        for line_number, row in enumerate(reader, start=2):  # row 1 is the header
            if _is_blank_row(row):
                logger.debug("%s:%d blank row skipped", path.name, line_number)
                continue
            seq = id_offset + len(rows) + 1
            rows.append(
                RawLead(
                    lead_id=f"L{seq:04d}",
                    source_file=path.name,
                    row_number=line_number,
                    name=_get(row, "name"),
                    company=_get(row, "company"),
                    company_size=_get(row, "company_size"),
                    industry=_get(row, "industry"),
                    source=_get(row, "source"),
                    last_interaction_date=_get(row, "last_interaction_date"),
                )
            )

    if not rows:
        logger.warning("%s: no data rows found", path.name)
    else:
        logger.info("%s: %d data rows", path.name, len(rows))
    return rows


def _get(row: dict[str, str | None], column: str) -> str | None:
    """Raw cell value with surrounding whitespace stripped; None if absent."""
    value = row.get(column)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _is_blank_row(row: dict[str, str | None]) -> bool:
    return all((v is None or not v.strip()) for v in row.values())
