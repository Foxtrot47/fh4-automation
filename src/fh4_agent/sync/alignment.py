"""Deterministic nearest-neighbour stream alignment."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AlignedSample(Generic[T]):  # noqa: UP046
    session_ns: int
    value: T
    delta_ns: int
    index: int


def align_nearest(  # noqa: UP047
    target_ns: int, samples: Sequence[T], *, max_delta_ns: int | None = None
) -> AlignedSample[T] | None:  # noqa: UP047
    """Choose the closest sample, breaking equal distances by earlier index."""
    if isinstance(target_ns, bool) or not isinstance(target_ns, int) or target_ns < 0:
        raise ValueError("target_ns must be a non-negative integer")
    if max_delta_ns is not None and (
        isinstance(max_delta_ns, bool)
        or not isinstance(max_delta_ns, int)
        or max_delta_ns < 0
    ):
        raise ValueError("max_delta_ns must be a non-negative integer")
    best: AlignedSample[T] | None = None
    for index, sample in enumerate(samples):
        stamp = getattr(sample, "session_ns", None)
        if not isinstance(stamp, int) or isinstance(stamp, bool) or stamp < 0:
            raise ValueError("samples must expose non-negative integer session_ns")
        delta = stamp - target_ns
        candidate = AlignedSample(stamp, sample, delta, index)
        if best is None or (abs(delta), index) < (abs(best.delta_ns), best.index):
            best = candidate
    if (
        best is not None
        and max_delta_ns is not None
        and abs(best.delta_ns) > max_delta_ns
    ):
        return None
    return best


def align_streams(  # noqa: UP047
    frame_samples: Sequence[T],
    controller_samples: Sequence[T],
    telemetry_samples: Sequence[T],
    *,
    max_delta_ns: int,
) -> list[tuple[T, T | None, T | None]]:  # noqa: UP047
    """Align each frame to controller and telemetry streams in frame order."""
    if max_delta_ns < 0:
        raise ValueError("max_delta_ns must be non-negative")
    result: list[tuple[T, T | None, T | None]] = []
    for frame in frame_samples:
        target = cast(Any, frame).session_ns
        controller = align_nearest(
            target, controller_samples, max_delta_ns=max_delta_ns
        )
        telemetry = align_nearest(target, telemetry_samples, max_delta_ns=max_delta_ns)
        result.append(
            (
                frame,
                controller.value if controller else None,
                telemetry.value if telemetry else None,
            )
        )
    return result


__all__ = ["AlignedSample", "align_nearest", "align_streams"]
