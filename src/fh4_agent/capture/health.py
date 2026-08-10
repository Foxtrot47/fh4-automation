"""Capture health summaries and deterministic latency percentiles."""

from __future__ import annotations

import math
from collections.abc import Iterable

from ..sync.health import (
    BoundedQueue,
    CaptureHealth,
    HealthCounters,
    QueueOverloadError,
)
from .recording import RecordingError


def percentile(values: Iterable[int | float], percentile: float) -> float:
    ordered = [float(value) for value in values]
    if any(not math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    ordered.sort()
    if not ordered:
        raise ValueError("cannot calculate percentile of empty values")
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    rank = (len(ordered) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def latency_summary(values: Iterable[int | float]) -> dict[str, float]:
    data = list(values)
    return {
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "p99": percentile(data, 99),
    }


__all__ = [
    "BoundedQueue",
    "CaptureHealth",
    "HealthCounters",
    "QueueOverloadError",
    "RecordingError",
    "latency_summary",
    "percentile",
]
