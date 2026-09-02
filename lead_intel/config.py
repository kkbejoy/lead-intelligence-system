"""Load and validate `config.yaml` into typed objects.

Fail loudly at startup on a malformed config rather than producing a subtly
wrong report later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when config.yaml is missing required keys or has bad values."""


# --------------------------------------------------------------------------- #
# Typed views over the config sections
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    batch_size: int
    temperature: float
    max_retries: int
    backoff_seconds: tuple[float, ...]
    request_timeout_seconds: float
    offline: bool


@dataclass(frozen=True)
class PathsConfig:
    output_report: Path
    output_csv: Path
    run_log: Path
    batch_log: Path


@dataclass(frozen=True)
class RubricConfig:
    weights: dict[str, float]
    qualified_min: float
    rejected_below: float
    missing_defaults: dict[str, float]
    forced_review: dict[str, Any]
    size_bands: tuple[dict[str, Any], ...]
    industry_tiers: dict[str, Any]
    source_scores: dict[str, Any]
    recency_bands: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AppConfig:
    evaluation_date_setting: str  # "auto" | "today" | "YYYY-MM-DD"
    paths: PathsConfig
    llm: LLMConfig
    rubric: RubricConfig
    missing_tokens: frozenset[str]

    def resolve_evaluation_date(self, interaction_dates: list[date]) -> date:
        """Turn the `evaluation_date` setting into a concrete date.

        `auto` -> the day after the most recent interaction in the batch, so a
        historical dataset still exercises the full recency curve.
        """
        setting = self.evaluation_date_setting.strip().lower()
        if setting == "today":
            return date.today()
        if setting == "auto":
            if not interaction_dates:
                return date.today()
            return max(interaction_dates) + timedelta(days=1)
        try:
            return datetime.strptime(self.evaluation_date_setting, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ConfigError(
                f"evaluation_date must be 'auto', 'today', or YYYY-MM-DD; "
                f"got {self.evaluation_date_setting!r}"
            ) from exc


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        cfg = _build(data)
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"config.yaml is missing or malformed near: {exc}") from exc

    _validate(cfg)
    return cfg


def _build(data: dict[str, Any]) -> AppConfig:
    llm_raw = data["llm"]
    paths_raw = data["paths"]
    rub = data["rubric"]
    bands = rub["decision_bands"]

    return AppConfig(
        evaluation_date_setting=str(data.get("evaluation_date", "auto")),
        paths=PathsConfig(
            output_report=Path(paths_raw["output_report"]),
            output_csv=Path(paths_raw["output_csv"]),
            run_log=Path(paths_raw["run_log"]),
            batch_log=Path(paths_raw["batch_log"]),
        ),
        llm=LLMConfig(
            provider=str(llm_raw["provider"]),
            model=str(llm_raw["model"]),
            batch_size=int(llm_raw["batch_size"]),
            temperature=float(llm_raw["temperature"]),
            max_retries=int(llm_raw["max_retries"]),
            backoff_seconds=tuple(float(s) for s in llm_raw["backoff_seconds"]),
            request_timeout_seconds=float(llm_raw["request_timeout_seconds"]),
            offline=bool(llm_raw.get("offline", False)),
        ),
        rubric=RubricConfig(
            weights={k: float(v) for k, v in rub["weights"].items()},
            qualified_min=float(bands["qualified_min"]),
            rejected_below=float(bands["rejected_below"]),
            missing_defaults={
                k: float(v) for k, v in rub["missing_defaults"].items()
            },
            forced_review=dict(rub["forced_review"]),
            size_bands=tuple(rub["size_bands"]),
            industry_tiers=rub["industry_tiers"],
            source_scores=rub["source_scores"],
            recency_bands=tuple(rub["recency_bands"]),
        ),
        missing_tokens=frozenset(
            str(t).strip().lower() for t in data.get("missing_tokens", [])
        ),
    )


def _validate(cfg: AppConfig) -> None:
    r = cfg.rubric
    fit_sum = r.weights.get("fit_size", 0) + r.weights.get("fit_industry", 0)
    final_sum = r.weights.get("final_fit", 0) + r.weights.get("final_intent", 0)
    if abs(fit_sum - 1.0) > 1e-6:
        raise ConfigError(f"fit_size + fit_industry must sum to 1.0 (got {fit_sum})")
    if abs(final_sum - 1.0) > 1e-6:
        raise ConfigError(
            f"final_fit + final_intent must sum to 1.0 (got {final_sum})"
        )
    if not (0 <= r.rejected_below <= r.qualified_min <= 10):
        raise ConfigError(
            "decision bands must satisfy 0 <= rejected_below <= "
            f"qualified_min <= 10 (got {r.rejected_below}, {r.qualified_min})"
        )
    if cfg.llm.batch_size < 1:
        raise ConfigError("llm.batch_size must be >= 1")
    if cfg.llm.max_retries < 1:
        raise ConfigError("llm.max_retries must be >= 1")
