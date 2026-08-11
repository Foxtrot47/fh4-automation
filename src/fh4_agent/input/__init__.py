"""Read-only physical input adapters."""

from .xinput import (
    XINPUT_GAMEPAD_A,
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
    "XINPUT_GAMEPAD_A",
    "XInputController",
    "XInputReader",
    "XInputState",
]
