"""Unsigned FH4 TimestampMS continuity and packet-loss diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

TIMESTAMP_MODULUS = 2**32
TIMESTAMP_HALF_RANGE = 2**31


@dataclass(frozen=True, slots=True)
class TimestampEstimate:
    timestamp_ms: int
    delta_ms: int | None
    kind: str
    estimated_missing_packets: int

    @property
    def is_duplicate(self) -> bool:
        return self.kind == "duplicate"

    @property
    def is_out_of_order(self) -> bool:
        return self.kind == "out_of_order"

    @property
    def is_wrap(self) -> bool:
        return self.kind == "wrap"


class TimestampContinuity:
    """Track unsigned 32-bit packet timestamps across wraps and gaps.

    ``expected_interval_ms`` is the nominal packet period.  A gap is counted
    only when the elapsed time exceeds that period by ``gap_tolerance_ms``.
    Backward jumps larger than half the uint32 range are out-of-order; a small
    backward value is a genuine uint32 wrap.
    """

    def __init__(
        self,
        *,
        expected_interval_ms: int = 16,
        gap_tolerance_ms: int = 2,
    ) -> None:
        if expected_interval_ms <= 0 or gap_tolerance_ms < 0:
            raise ValueError(
                "expected interval must be positive and tolerance non-negative"
            )
        self.expected_interval_ms = expected_interval_ms
        self.gap_tolerance_ms = gap_tolerance_ms
        self.previous: int | None = None
        self.packets = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.wraps = 0
        self.estimated_missing_packets = 0

    def update(self, timestamp_ms: int) -> TimestampEstimate:
        if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool):
            raise ValueError("timestamp_ms must be an unsigned integer")
        if not 0 <= timestamp_ms < TIMESTAMP_MODULUS:
            raise ValueError("timestamp_ms must fit uint32")
        self.packets += 1
        if self.previous is None:
            self.previous = timestamp_ms
            return TimestampEstimate(timestamp_ms, None, "first", 0)
        previous = self.previous
        raw_delta = timestamp_ms - previous
        delta = raw_delta & 0xFFFFFFFF
        signed_delta = (
            delta if delta < TIMESTAMP_HALF_RANGE else delta - TIMESTAMP_MODULUS
        )
        if delta == 0:
            kind = "duplicate"
            missing = 0
            self.duplicates += 1
        elif delta >= TIMESTAMP_HALF_RANGE:
            kind = "out_of_order"
            missing = 0
            self.out_of_order += 1
        else:
            kind = "wrap" if raw_delta < 0 else "forward"
            if kind == "wrap":
                self.wraps += 1
            missing = max(
                0,
                round(delta / self.expected_interval_ms) - 1
                if delta > self.expected_interval_ms + self.gap_tolerance_ms
                else 0,
            )
            self.estimated_missing_packets += missing
            # Do not let a stale out-of-order packet become the new baseline.
            self.previous = timestamp_ms
        return TimestampEstimate(timestamp_ms, signed_delta, kind, missing)

    def reset(self) -> None:
        self.previous = None

    def diagnostics(self) -> dict[str, int]:
        return {
            "packets": self.packets,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "wraps": self.wraps,
            "estimated_missing_packets": self.estimated_missing_packets,
        }


TimestampTracker = TimestampContinuity

__all__ = ["TimestampContinuity", "TimestampEstimate", "TimestampTracker"]
