"""Typed stream envelopes carrying one lossless integer alignment clock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from .timeline import TimelineStamp

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StreamSample(Generic[T]):  # noqa: UP046
    value: T
    timestamp: TimelineStamp

    @property
    def session_ns(self) -> int:
        return self.timestamp.session_ns

    @property
    def source_clock_ns(self) -> int:
        if self.timestamp.source_clock_ns is not None:
            return self.timestamp.source_clock_ns
        return self.timestamp.monotonic_ns

    @property
    def game_clock_ms(self) -> int | None:
        return self.timestamp.game_clock_ms


SynchronizedSample = StreamSample

__all__ = ["StreamSample", "SynchronizedSample"]
