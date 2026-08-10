"""Bounded queue and capture health accounting."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class HealthCounters:
    dropped: int = 0
    stale: int = 0
    disconnects: int = 0
    writer_faults: int = 0
    accepted: int = 0
    overload_failures: int = 0

    def to_mapping(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


class QueueOverloadError(RuntimeError):
    """A lossless stream cannot accept another item."""


class BoundedQueue(Generic[T]):  # noqa: UP046
    """Explicitly bounded queue with observable drop policy."""

    def __init__(self, capacity: int, *, policy: str = "reject") -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be positive")
        if policy not in {"reject", "drop_oldest", "drop_newest"}:
            raise ValueError("policy must be reject, drop_oldest, or drop_newest")
        self.capacity = capacity
        self.policy = policy
        self._values: deque[T] = deque()
        self.health = HealthCounters()

    def put(self, value: T) -> bool:
        if len(self._values) >= self.capacity:
            if self.policy == "reject":
                self.health.overload_failures += 1
                raise QueueOverloadError("bounded queue is full")
            if self.policy == "drop_newest":
                self.health.dropped += 1
                return False
            self._values.popleft()
            self.health.dropped += 1
        self._values.append(value)
        self.health.accepted += 1
        return True

    def get(self) -> T:
        if not self._values:
            raise IndexError("bounded queue is empty")
        return self._values.popleft()

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    def snapshot(self) -> dict[str, object]:
        return {
            "capacity": self.capacity,
            "policy": self.policy,
            **self.health.to_mapping(),
            "queued": len(self),
        }


CaptureHealth = HealthCounters

__all__ = ["BoundedQueue", "CaptureHealth", "HealthCounters", "QueueOverloadError"]
