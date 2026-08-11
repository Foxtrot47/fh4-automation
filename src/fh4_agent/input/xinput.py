"""Read-only physical XInput sampling and controller selection."""
# ruff: noqa: E501

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import ControllerSample, RequestedControlAction
from ..sync import SessionClock, TimelineStamp

XINPUT_GAMEPAD_A = 0x1000
_XINPUT_SLOTS = range(4)


class ControllerError(RuntimeError):
    """Base error for physical XInput discovery and sampling."""


class ControllerDisconnected(ControllerError):
    """The configured physical controller is not available."""


class ControllerSelectionError(ControllerError):
    """A unique physical XInput controller could not be selected."""


def _validate_slot(slot: int) -> int:
    if isinstance(slot, bool) or not isinstance(slot, int) or slot not in _XINPUT_SLOTS:
        raise ValueError("XInput slot must be an integer from 0 through 3")
    return slot


@dataclass(frozen=True, slots=True)
class XInputState:
    buttons: int = 0
    left_trigger: int = 0
    right_trigger: int = 0
    thumb_lx: int = 0
    thumb_ly: int = 0
    thumb_rx: int = 0
    thumb_ry: int = 0
    packet: int = 0

    def to_action(self, deadzone: int = 7849) -> RequestedControlAction:
        def axis(value: int) -> float:
            if abs(value) <= deadzone:
                return 0.0
            limit = 32767.0 if value >= 0 else 32768.0
            return max(-1.0, min(1.0, value / limit))

        return RequestedControlAction(
            steering=axis(self.thumb_lx),
            throttle=self.right_trigger / 255.0,
            brake=self.left_trigger / 255.0,
            handbrake=1.0 if self.buttons & XINPUT_GAMEPAD_A else 0.0,
        )

    def to_mapping(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


class PhysicalControllerSource(Protocol):
    def samples(
        self, *, max_samples: int | None = None
    ) -> Iterator[ControllerSample]: ...
    def close(self) -> None: ...


class XInputController:
    """Windows adapter resolving only XInputGetState, lazily."""

    def __init__(
        self,
        clock: SessionClock,
        *,
        slot: int = 0,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        poll_hz: int = 125,
    ) -> None:
        if isinstance(poll_hz, bool) or not isinstance(poll_hz, int) or poll_hz <= 0:
            raise ValueError("poll_hz must be positive")
        self.clock = clock
        self.slot = _validate_slot(slot)
        self.poll_hz = poll_hz
        self._monotonic_ns = monotonic_ns
        self._dll: Any = None
        self._get_state: Any = None
        self._closed = False
        self.disconnect_count = 0
        self.last_state: XInputState | None = None

    def _ensure_api(self) -> Any:
        if self._closed:
            raise RuntimeError("controller is closed")
        if self._get_state is None:
            if sys.platform != "win32":
                raise RuntimeError("XInput is available only on Windows")
            self._dll = ctypes.WinDLL("xinput1_4.dll")
            self._get_state = self._dll.XInputGetState
            self._get_state.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            self._get_state.restype = ctypes.c_uint
        return self._get_state

    def read_state(self) -> tuple[XInputState, TimelineStamp]:
        class Gamepad(ctypes.Structure):
            _fields_ = [
                ("buttons", ctypes.c_uint16),
                ("left_trigger", ctypes.c_uint8),
                ("right_trigger", ctypes.c_uint8),
                ("thumb_lx", ctypes.c_int16),
                ("thumb_ly", ctypes.c_int16),
                ("thumb_rx", ctypes.c_int16),
                ("thumb_ry", ctypes.c_int16),
            ]

        class State(ctypes.Structure):
            _fields_ = [("packet", ctypes.c_uint32), ("gamepad", Gamepad)]

        raw = State()
        timestamp = self._monotonic_ns()
        result = self._ensure_api()(self.slot, ctypes.byref(raw))
        stamp = self.clock.stamp(timestamp, source_clock_ns=timestamp)
        if result != 0:
            self.disconnect_count += 1
            raise ControllerDisconnected(
                f"XInput slot {self.slot} failed with status {result}"
            )
        pad = raw.gamepad
        state = XInputState(
            buttons=pad.buttons,
            left_trigger=pad.left_trigger,
            right_trigger=pad.right_trigger,
            thumb_lx=pad.thumb_lx,
            thumb_ly=pad.thumb_ly,
            thumb_rx=pad.thumb_rx,
            thumb_ry=pad.thumb_ry,
            packet=raw.packet,
        )
        self.last_state = state
        return state, stamp

    def sample(self) -> tuple[XInputState, TimelineStamp]:
        return self.read_state()

    def samples(self, *, max_samples: int | None = None) -> Iterator[ControllerSample]:
        if max_samples is not None and (
            isinstance(max_samples, bool) or max_samples <= 0
        ):
            raise ValueError("max_samples must be positive")
        count, period = 0, 1_000_000_000 // self.poll_hz
        next_poll = self._monotonic_ns()
        while max_samples is None or count < max_samples:
            now = self._monotonic_ns()
            if now < next_poll:
                time.sleep((next_poll - now) / 1e9)
            state, stamp = self.read_state()
            yield ControllerSample(
                stamp.session_ns / 1e9,
                state.to_action(),
                session_ns=stamp.session_ns,
                source_clock_ns=stamp.source_clock_ns,
                packet=state.packet,
                raw_state=state.to_mapping(),
            )
            count += 1
            next_poll += period

    def close(self) -> None:
        self._get_state = self._dll = None
        self._closed = True

    def __enter__(self) -> XInputController:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def connected_xinput_slots() -> tuple[int, ...]:
    """Return all currently readable XInput slots without retaining devices."""
    clock = SessionClock()
    connected: list[int] = []
    for slot in _XINPUT_SLOTS:
        controller = XInputController(clock, slot=slot)
        try:
            controller.read_state()
        except ControllerDisconnected:
            pass
        else:
            connected.append(slot)
        finally:
            controller.close()
    return tuple(connected)


def resolve_xinput_slot(requested: int | None = None) -> int:
    """Resolve an explicit slot or require exactly one connected controller."""
    if requested is not None:
        _validate_slot(requested)
    connected = connected_xinput_slots()
    if requested is not None:
        if requested in connected:
            return requested
        available = ", ".join(str(slot) for slot in connected) or "none"
        raise ControllerSelectionError(
            f"XInput slot {requested} is not connected; connected slots: {available}"
        )
    if len(connected) == 1:
        return connected[0]
    if not connected:
        raise ControllerSelectionError("no connected XInput controller was found")
    slots = ", ".join(str(slot) for slot in connected)
    raise ControllerSelectionError(
        f"multiple XInput controllers are connected at slots {slots}; "
        "pass --controller-slot"
    )


class FakeController:
    def __init__(self, states: list[XInputState], clock: SessionClock) -> None:
        self.states, self.clock, self._closed = list(states), clock, False

    def samples(self, *, max_samples: int | None = None) -> Iterator[ControllerSample]:
        values = self.states if max_samples is None else self.states[:max_samples]
        for state in values:
            if self._closed:
                return
            stamp = self.clock.stamp(source_clock_ns=self.clock.origin_monotonic_ns)
            yield ControllerSample(
                stamp.session_ns / 1e9,
                state.to_action(),
                session_ns=stamp.session_ns,
                source_clock_ns=stamp.source_clock_ns,
                packet=state.packet,
                raw_state=state.to_mapping(),
            )

    def close(self) -> None:
        self._closed = True


XInputReader = XInputController
__all__ = [
    "ControllerDisconnected",
    "ControllerError",
    "ControllerSelectionError",
    "FakeController",
    "PhysicalControllerSource",
    "XInputController",
    "XInputReader",
    "XINPUT_GAMEPAD_A",
    "XInputState",
    "connected_xinput_slots",
    "resolve_xinput_slot",
]
