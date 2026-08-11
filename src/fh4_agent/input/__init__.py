"""Read-only physical input adapters."""

from .xinput import (
    XINPUT_GAMEPAD_A,
    ControllerDisconnected,
    ControllerError,
    ControllerSelectionError,
    FakeController,
    PhysicalControllerSource,
    XInputController,
    XInputReader,
    XInputState,
    connected_xinput_slots,
    resolve_xinput_slot,
)

__all__ = [
    "ControllerDisconnected",
    "ControllerError",
    "ControllerSelectionError",
    "FakeController",
    "PhysicalControllerSource",
    "XINPUT_GAMEPAD_A",
    "XInputController",
    "XInputReader",
    "XInputState",
    "connected_xinput_slots",
    "resolve_xinput_slot",
]
