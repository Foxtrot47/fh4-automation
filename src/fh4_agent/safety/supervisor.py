"""Deterministic arming gate between requested actions and a backend."""

from __future__ import annotations

from ..contracts import (
    BenchmarkConfig,
    RequestedControlAction,
    RuntimeArmingState,
    neutral_action,
)
from ..controls.backend import ControllerBackend


class SafetySupervisor:
    """Route only explicitly armed requests; neutralize all other states."""

    def __init__(self, backend: ControllerBackend) -> None:
        self.backend = backend
        self.state = RuntimeArmingState.disarmed()

    def arm(self, benchmark: BenchmarkConfig | None = None) -> None:
        """Arm only with a config that has already passed validation.

        A missing config is treated as a safe failed-arm attempt.  It leaves
        the supervisor disarmed and neutralizes output rather than allowing a
        caller to bypass the typed evidence requirement.
        """
        if benchmark is None:
            self.state = RuntimeArmingState.disarmed(
                "validated BenchmarkConfig required"
            )
            self.backend.neutralize()
            return
        if not isinstance(benchmark, BenchmarkConfig):
            raise TypeError("benchmark must be validated BenchmarkConfig")
        self.state = RuntimeArmingState.armed(benchmark)

    def disarm(self, reason: str = "explicitly disarmed") -> None:
        self.state = RuntimeArmingState.disarmed(reason)
        self.backend.neutralize()

    def fault(self, reason: str) -> None:
        self.state = RuntimeArmingState.faulted(reason)
        self.backend.neutralize()

    def submit(self, action: RequestedControlAction) -> RequestedControlAction:
        """Emit action when armed, otherwise emit neutral and return what emitted."""
        if not isinstance(action, RequestedControlAction):
            raise TypeError("action must be RequestedControlAction")
        emitted = action if self.state.can_emit else neutral_action()
        self.backend.send(emitted)
        return emitted
