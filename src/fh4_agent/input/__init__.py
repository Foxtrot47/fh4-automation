"""Read-only physical input adapters."""

from .xinput import (
    ControllerDisconnected,
    FakeController,
    PhysicalControllerSource,
    XInputController,
    XInputReader,
    XInputState,
)

__all__ = [
    "ControllerDisconnected",
    "FakeController",
    "PhysicalControllerSource",
    "XInputController",
    "XInputReader",
    "XInputState",
]
