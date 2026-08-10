"""Replaceable controller backends.

Only this interface may eventually emit device state.  Phase 0 ships a dry-run
implementation that records requests in memory and performs no device I/O.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Final

from ..contracts import RequestedControlAction, neutral_action


class ControllerBackend(ABC):
    """Interface for a controller output sink."""

    @property
    @abstractmethod
    def last_action(self) -> RequestedControlAction:
        """Return the last action accepted by this backend."""

    @abstractmethod
    def send(self, action: RequestedControlAction) -> None:
        """Accept an action for output."""

    @abstractmethod
    def neutralize(self) -> None:
        """Release all controls."""

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""


class DryRunControllerBackend(ControllerBackend):
    """In-memory backend that can never write controller/device state."""

    DEVICE_WRITES: Final[int] = 0

    def __init__(self) -> None:
        self._last_action = neutral_action()
        self.history: list[RequestedControlAction] = []
        self.closed = False

    @property
    def last_action(self) -> RequestedControlAction:
        return self._last_action

    @property
    def device_writes(self) -> int:
        """Always zero; useful as an explicit dry-run assertion."""
        return self.DEVICE_WRITES

    def send(self, action: RequestedControlAction) -> None:
        if self.closed:
            raise RuntimeError("controller backend is closed")
        if not isinstance(action, RequestedControlAction):
            raise TypeError("action must be RequestedControlAction")
        self._last_action = action
        self.history.append(action)

    def neutralize(self) -> None:
        self.send(neutral_action())

    def close(self) -> None:
        self.closed = True
        self._last_action = neutral_action()


# Short alias for callers that refer to the implementation by its mode.
DryRunBackend = DryRunControllerBackend

__all__ = ["ControllerBackend", "DryRunBackend", "DryRunControllerBackend"]
