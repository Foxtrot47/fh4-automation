"""Shared integer monotonic timeline for cross-stream alignment."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class TimelineStamp:
    """A sample's session-relative clock plus retained source clocks."""

    session_ns: int
    monotonic_ns: int
    source_clock_ns: int | None = None
    game_clock_ms: int | None = None

    def __post_init__(self) -> None:
        for name in ("session_ns", "monotonic_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source_clock_ns is not None and (
            isinstance(self.source_clock_ns, bool)
            or not isinstance(self.source_clock_ns, int)
            or self.source_clock_ns < 0
        ):
            raise ValueError("source_clock_ns must be a non-negative integer")
        if self.game_clock_ms is not None and (
            isinstance(self.game_clock_ms, bool)
            or not isinstance(self.game_clock_ms, int)
            or self.game_clock_ms < 0
        ):
            raise ValueError("game_clock_ms must be a non-negative integer")


class SessionClock:
    """Convert one monotonic clock into integer session-relative nanoseconds."""

    def __init__(self, monotonic_ns: Callable[[], int] = time.monotonic_ns) -> None:
        origin = monotonic_ns()
        if isinstance(origin, bool) or not isinstance(origin, int):
            raise ValueError("monotonic clock must return an integer")
        self._monotonic_ns = monotonic_ns
        self.origin_monotonic_ns = origin
        self._lock = threading.Lock()

    def stamp(
        self,
        monotonic_ns: int | None = None,
        *,
        source_clock_ns: int | None = None,
        game_clock_ms: int | None = None,
    ) -> TimelineStamp:
        with self._lock:
            value = self._monotonic_ns() if monotonic_ns is None else monotonic_ns
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("monotonic timestamp must be an integer")
            relative = value - self.origin_monotonic_ns
        if relative < 0:
            raise ValueError("monotonic timestamp precedes session origin")
        return TimelineStamp(relative, value, source_clock_ns, game_clock_ms)

    def now_ns(self) -> int:
        return self.stamp().session_ns


# Names used by callers that describe this as a clock rather than a timeline.
MonotonicTimeline = SessionClock
SessionTimestamp = TimelineStamp

__all__ = ["MonotonicTimeline", "SessionClock", "SessionTimestamp", "TimelineStamp"]
