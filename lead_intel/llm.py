"""LLM client boundary.

`DecisionClient` is the interface the pipeline depends on; two implementations:

* `GroqDecisionClient` — real API calls with retry/backoff and strict output
  validation (pipeline spec §3.5).
* `MockDecisionClient` — deterministic, offline; derives a sensible decision
  from the rubric evidence so the whole pipeline runs with no API key. Used
  for tests, CI, and `--offline`.

Both take a *batch* (list of lead payload dicts) and return a list of decision
dicts keyed by `lead_id`. Unrecoverable failures raise `LLMBatchError`, which
the caller catches per batch so one bad batch never stops the run.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

from .config import LLMConfig
from .models import DECISIONS, QUALIFIED, REJECTED, REVIEW
from .prompts import SYSTEM_PROMPT, build_user_message
from .utils import strip_code_fences

logger = logging.getLogger(__name__)


class LLMBatchError(RuntimeError):
    """Raised when a batch cannot be completed after all retries."""


class DecisionClient(Protocol):
    def decide_batch(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one dict per input lead: lead_id, decision, reasoning,
        outreach_message. Raises LLMBatchError on unrecoverable failure."""


# --------------------------------------------------------------------------- #
# Real client
# --------------------------------------------------------------------------- #
class GroqDecisionClient:
    def __init__(self, cfg: LLMConfig, api_key: str) -> None:
        # Imported lazily so the package works (tests, offline) without the dep.
        from groq import Groq

        self._cfg = cfg
        self._client = Groq(api_key=api_key, timeout=cfg.request_timeout_seconds)

    def decide_batch(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                raw = self._call_once(payload)
                return _validate_results(raw, expected_ids=[p["lead_id"] for p in payload])
            except _RETRYABLE as exc:
                last_error = exc
                wait = self._backoff_for(attempt)
                logger.warning(
                    "LLM batch attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    self._cfg.max_retries,
                    exc.__class__.__name__,
                    wait,
                )
                time.sleep(wait)
            except _NON_RETRYABLE as exc:  # bad request, auth — retrying won't help
                raise LLMBatchError(f"non-retryable LLM error: {exc}") from exc

        raise LLMBatchError(
            f"batch failed after {self._cfg.max_retries} attempts: {last_error}"
        ) from last_error

    def _call_once(self, payload: list[dict[str, Any]]) -> str:
        response = self._client.chat.completions.create(
            model=self._cfg.model,
            temperature=self._cfg.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(payload)},
            ],
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise _SchemaError("empty response content")
        return content

    def _backoff_for(self, attempt: int) -> float:
        seq = self._cfg.backoff_seconds
        return seq[min(attempt - 1, len(seq) - 1)] if seq else 2.0 ** (attempt - 1)


# --------------------------------------------------------------------------- #
# Offline mock
# --------------------------------------------------------------------------- #
class MockDecisionClient:
    """Rubric-driven decisions, no network. Applies the §4.1 forced-review
    rule directly and templates a personalised message for qualified leads."""

    def decide_batch(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._decide_one(p) for p in payload]

    @staticmethod
    def _decide_one(p: dict[str, Any]) -> dict[str, Any]:
        evidence = p.get("evidence", {})
        flags = set(evidence.get("data_quality_flags", []))
        lighter = {"industry_missing", "source_missing", "date_missing", "date_invalid"}

        if "company_size_missing" in flags or len(flags & lighter) >= 2:
            decision, why = REVIEW, "forced review: key fields missing/defaulted"
        else:
            decision = evidence.get("rubric_band", REVIEW)
            why = f"rubric band {decision} at final_score {evidence.get('final_score')}"

        message = ""
        if decision == QUALIFIED:
            message = _mock_message(p)
        return {
            "lead_id": p["lead_id"],
            "decision": decision,
            "reasoning": f"[offline] {why}",
            "outreach_message": message,
        }


def _mock_message(p: dict[str, Any]) -> str:
    fields = p.get("fields", {})
    name = fields.get("name")
    company = fields.get("company") or "your team"
    industry = fields.get("industry") or "your space"
    source = (fields.get("source") or "your recent enquiry").lower()
    greeting = f"Hi {name}," if name else f"Hi {company} team,"
    return (
        f"{greeting} thanks for {('your ' + source) if 'request' in source or 'download' in source else f'connecting via {source}'}. "
        f"We work with {industry} companies around {company}'s size on the same "
        f"workflows your team is evaluating. Worth a short call this week to see "
        f"if it maps to what you need?"
    )


# --------------------------------------------------------------------------- #
# Output validation — malformed output is treated like a call failure (§3.5)
# --------------------------------------------------------------------------- #
class _SchemaError(ValueError):
    """LLM returned something that isn't the agreed schema."""


def _validate_results(
    raw_text: str, *, expected_ids: list[str]
) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(strip_code_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise _SchemaError(f"response is not valid JSON: {exc}") from exc

    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list) or not results:
        raise _SchemaError("response JSON has no non-empty 'results' array")

    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise _SchemaError(f"result entry is not an object: {item!r}")
        lead_id = str(item.get("lead_id", "")).strip()
        decision = str(item.get("decision", "")).strip().lower()
        if decision not in DECISIONS:
            raise _SchemaError(f"bad decision {decision!r} for {lead_id or '?'}")
        by_id[lead_id] = {
            "lead_id": lead_id,
            "decision": decision,
            "reasoning": str(item.get("reasoning", "")).strip(),
            "outreach_message": str(item.get("outreach_message", "") or "").strip(),
        }

    missing = [i for i in expected_ids if i not in by_id]
    if missing:
        raise _SchemaError(f"response omitted {len(missing)} lead(s): {missing[:5]}")
    # Return in the original batch order.
    return [by_id[i] for i in expected_ids]


# Which exceptions are worth retrying. Kept as module-level tuples so the retry
# loop stays readable; resolved lazily because `groq` may not be installed.
def _load_groq_error_types() -> tuple[tuple[type, ...], tuple[type, ...]]:
    try:
        import groq

        retryable = (
            groq.APIConnectionError,
            groq.APITimeoutError,
            groq.RateLimitError,
            groq.InternalServerError,
            _SchemaError,  # give the model another try to format correctly
        )
        non_retryable = (
            groq.AuthenticationError,
            groq.PermissionDeniedError,
            groq.BadRequestError,
            groq.NotFoundError,
        )
        return retryable, non_retryable
    except Exception:  # pragma: no cover - only when groq isn't importable
        return (_SchemaError,), tuple()


_RETRYABLE, _NON_RETRYABLE = _load_groq_error_types()
