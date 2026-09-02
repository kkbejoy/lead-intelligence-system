"""Small cross-cutting helpers: logging setup, batching, text cleanup."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

_LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"


def configure_logging(log_file: Path, *, verbose: bool = False) -> None:
    """Send logs to both the console and a run log file.

    The run log is a persistent, timestamped record so a batch failure can be
    diagnosed after the run finishes (pipeline spec §3.5).
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Reset handlers so repeated calls (e.g. in tests) don't stack up.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # the file always keeps full detail
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)


def chunked(items: list[T], size: int) -> Iterator[list[T]]:
    """Yield consecutive sub-lists of at most `size` items."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    for start in range(0, len(items), size):
        yield items[start : start + size]


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def strip_code_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if the model added one."""
    return _FENCE_RE.sub("", text).strip()


def as_iterable(value: Iterable[T] | None) -> Iterable[T]:
    """Treat None as an empty iterable — avoids `or []` noise at call sites."""
    return () if value is None else value
