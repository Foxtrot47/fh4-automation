"""Strict decoder for the little-endian FH4 Horizon Data Out packet.

The game has two observed datagram sizes: the packed 323-byte packet and the
same packet with one unknown trailing byte (324 bytes).  The Horizon-specific
12-byte region and optional trailing byte intentionally remain opaque.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, fields
from typing import Any

from ..contracts import TelemetrySample

PACKET_SIZE = 323
PACKET_SIZE_WITH_TRAILING_BYTE = 324
PACKET_LENGTHS = frozenset({PACKET_SIZE, PACKET_SIZE_WITH_TRAILING_BYTE})


class TelemetryDecodeError(ValueError):
    """Raised when a datagram is not a valid supported FH4 packet."""


@dataclass(frozen=True, slots=True)
class NormalizedControls:
    """FH4 byte controls converted to the shared [-1, 1]/[0, 1] ranges."""

    steering: float
    throttle: float
    brake: float
    handbrake: float

    def to_action(self) -> Any:
        from ..contracts import RequestedControlAction

        return RequestedControlAction(
            self.steering, self.throttle, self.brake, self.handbrake
        )


@dataclass(frozen=True, slots=True)
class FH4TelemetryPacket:
    """All typed fields in the packed FH4 packet.

    ``horizon_extra`` and ``optional_trailing_byte`` are retained as bytes but
    have no semantic meaning until a live capture confirms their behavior.
    """

    is_race_on: bool
    timestamp_ms: int
    engine_max_rpm: float
    engine_idle_rpm: float
    current_engine_rpm: float
    acceleration_x: float
    acceleration_y: float
    acceleration_z: float
    velocity_x: float
    velocity_y: float
    velocity_z: float
    angular_velocity_x: float
    angular_velocity_y: float
    angular_velocity_z: float
    yaw: float
    pitch: float
    roll: float
    normalized_suspension_travel_front_left: float
    normalized_suspension_travel_front_right: float
    normalized_suspension_travel_rear_left: float
    normalized_suspension_travel_rear_right: float
    tire_slip_ratio_front_left: float
    tire_slip_ratio_front_right: float
    tire_slip_ratio_rear_left: float
    tire_slip_ratio_rear_right: float
    wheel_rotation_speed_front_left: float
    wheel_rotation_speed_front_right: float
    wheel_rotation_speed_rear_left: float
    wheel_rotation_speed_rear_right: float
    wheel_on_rumble_strip_front_left: int
    wheel_on_rumble_strip_front_right: int
    wheel_on_rumble_strip_rear_left: int
    wheel_on_rumble_strip_rear_right: int
    wheel_in_puddle_depth_front_left: float
    wheel_in_puddle_depth_front_right: float
    wheel_in_puddle_depth_rear_left: float
    wheel_in_puddle_depth_rear_right: float
    surface_rumble_front_left: float
    surface_rumble_front_right: float
    surface_rumble_rear_left: float
    surface_rumble_rear_right: float
    tire_slip_angle_front_left: float
    tire_slip_angle_front_right: float
    tire_slip_angle_rear_left: float
    tire_slip_angle_rear_right: float
    tire_combined_slip_front_left: float
    tire_combined_slip_front_right: float
    tire_combined_slip_rear_left: float
    tire_combined_slip_rear_right: float
    suspension_travel_meters_front_left: float
    suspension_travel_meters_front_right: float
    suspension_travel_meters_rear_left: float
    suspension_travel_meters_rear_right: float
    car_ordinal: int
    car_class: int
    car_performance_index: int
    drivetrain_type: int
    num_cylinders: int
    horizon_extra: bytes
    position_x: float
    position_y: float
    position_z: float
    speed: float
    power: float
    torque: float
    tire_temp_front_left: float
    tire_temp_front_right: float
    tire_temp_rear_left: float
    tire_temp_rear_right: float
    boost: float
    fuel: float
    distance_traveled: float
    best_lap: float
    last_lap: float
    current_lap: float
    current_race_time: float
    lap_number: int
    race_position: int
    accel: int
    brake: int
    clutch: int
    handbrake: int
    gear: int
    steer: int
    normalized_driving_line: int
    normalized_ai_brake_difference: int
    optional_trailing_byte: bytes = b""

    @property
    def timestamp_s(self) -> float:
        return self.timestamp_ms / 1000.0

    @property
    def steering_normalized(self) -> float:
        return max(-1.0, min(1.0, self.steer / 127.0))

    @property
    def throttle_normalized(self) -> float:
        return self.accel / 255.0

    @property
    def brake_normalized(self) -> float:
        return self.brake / 255.0

    @property
    def clutch_normalized(self) -> float:
        return self.clutch / 255.0

    @property
    def handbrake_normalized(self) -> float:
        return self.handbrake / 255.0

    @property
    def normalized_controls(self) -> NormalizedControls:
        return NormalizedControls(
            self.steering_normalized,
            self.throttle_normalized,
            self.brake_normalized,
            self.handbrake_normalized,
        )

    @property
    def controls(self) -> dict[str, float]:
        """Return packet controls in the normalized action convention."""
        return {
            "steering": self.steering_normalized,
            "throttle": self.throttle_normalized,
            "brake": self.brake_normalized,
            "handbrake": self.handbrake_normalized,
        }

    def to_control_action(self) -> Any:
        """Adapt normalized telemetry controls to a control contract."""
        return self.normalized_controls.to_action()

    def to_telemetry_sample(
        self, timestamp_monotonic_s: float | None = None
    ) -> TelemetrySample:
        """Adapt ego fields to the shared sample contract.

        A receiver timestamp is preferred; falling back to the game clock is
        useful for offline decoding and is deterministic.
        """
        return TelemetrySample(
            timestamp=(
                self.timestamp_s
                if timestamp_monotonic_s is None
                else timestamp_monotonic_s
            ),
            speed_mps=max(0.0, self.speed),
            distance_m=max(0.0, self.distance_traveled),
            engine_rpm=max(0.0, self.current_engine_rpm),
            lap_number=self.lap_number,
            is_race_on=self.is_race_on,
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            result[field.name] = value.hex() if isinstance(value, bytes) else value
        return result


# The format is intentionally explicit rather than relying on native alignment.
_HEADER = struct.Struct("<iI")
_FLOAT = struct.Struct("<f")
_INT32 = struct.Struct("<i")
_U16 = struct.Struct("<H")
_U8 = struct.Struct("<B")
_S8 = struct.Struct("<b")

_PREFIX_FLOAT_NAMES = """
engine_max_rpm engine_idle_rpm current_engine_rpm
acceleration_x acceleration_y acceleration_z
velocity_x velocity_y velocity_z
angular_velocity_x angular_velocity_y angular_velocity_z
yaw pitch roll
normalized_suspension_travel_front_left normalized_suspension_travel_front_right
normalized_suspension_travel_rear_left normalized_suspension_travel_rear_right
tire_slip_ratio_front_left tire_slip_ratio_front_right
tire_slip_ratio_rear_left tire_slip_ratio_rear_right
wheel_rotation_speed_front_left wheel_rotation_speed_front_right
wheel_rotation_speed_rear_left wheel_rotation_speed_rear_right
""".split()
_MID_FLOAT_NAMES = """
wheel_in_puddle_depth_front_left wheel_in_puddle_depth_front_right
wheel_in_puddle_depth_rear_left wheel_in_puddle_depth_rear_right
surface_rumble_front_left surface_rumble_front_right
surface_rumble_rear_left surface_rumble_rear_right
tire_slip_angle_front_left tire_slip_angle_front_right
tire_slip_angle_rear_left tire_slip_angle_rear_right
tire_combined_slip_front_left tire_combined_slip_front_right
tire_combined_slip_rear_left tire_combined_slip_rear_right
suspension_travel_meters_front_left suspension_travel_meters_front_right
suspension_travel_meters_rear_left suspension_travel_meters_rear_right
""".split()
_POST_FLOAT_NAMES = """
position_x position_y position_z speed power torque
tire_temp_front_left tire_temp_front_right tire_temp_rear_left tire_temp_rear_right
boost fuel distance_traveled best_lap last_lap current_lap current_race_time
""".split()
_WHEEL_RUMBLE_NAMES = """
wheel_on_rumble_strip_front_left wheel_on_rumble_strip_front_right
wheel_on_rumble_strip_rear_left wheel_on_rumble_strip_rear_right
""".split()
_INT_NAMES = (
    "car_ordinal car_class car_performance_index drivetrain_type num_cylinders".split()
)


def decode_packet(data: bytes | bytearray | memoryview) -> FH4TelemetryPacket:
    """Decode exactly one supported FH4 datagram."""
    raw = bytes(data)
    if len(raw) not in PACKET_LENGTHS:
        raise TelemetryDecodeError(
            f"unsupported FH4 packet length {len(raw)}; expected 323 or 324 bytes"
        )
    offset = 0
    race, timestamp = _HEADER.unpack_from(raw, offset)
    offset += _HEADER.size
    if race not in (0, 1):
        raise TelemetryDecodeError(f"invalid IsRaceOn flag {race}; expected 0 or 1")
    values: dict[str, object] = {"is_race_on": bool(race), "timestamp_ms": timestamp}

    for name in _PREFIX_FLOAT_NAMES:
        value = _FLOAT.unpack_from(raw, offset)[0]
        offset += 4
        if not math.isfinite(value):
            raise TelemetryDecodeError(f"non-finite float in {name}")
        values[name] = value
    for name in _WHEEL_RUMBLE_NAMES:
        values[name] = _INT32.unpack_from(raw, offset)[0]
        offset += 4
    for name in _MID_FLOAT_NAMES:
        value = _FLOAT.unpack_from(raw, offset)[0]
        offset += 4
        if not math.isfinite(value):
            raise TelemetryDecodeError(f"non-finite float in {name}")
        values[name] = value
    for name in _INT_NAMES:
        values[name] = _INT32.unpack_from(raw, offset)[0]
        offset += 4
    values["horizon_extra"] = raw[offset : offset + 12]
    offset += 12
    for name in _POST_FLOAT_NAMES:
        value = _FLOAT.unpack_from(raw, offset)[0]
        offset += 4
        if not math.isfinite(value):
            raise TelemetryDecodeError(f"non-finite float in {name}")
        values[name] = value
    values["lap_number"] = _U16.unpack_from(raw, offset)[0]
    offset += 2
    for name in ("race_position", "accel", "brake", "clutch", "handbrake", "gear"):
        values[name] = _U8.unpack_from(raw, offset)[0]
        offset += 1
    for name in ("steer", "normalized_driving_line", "normalized_ai_brake_difference"):
        values[name] = _S8.unpack_from(raw, offset)[0]
        offset += 1
    if offset != PACKET_SIZE:
        raise TelemetryDecodeError(f"internal layout consumed {offset} bytes")
    values["optional_trailing_byte"] = raw[PACKET_SIZE:]
    return FH4TelemetryPacket(**values)  # type: ignore[arg-type]


# Friendly aliases used by callers that call the object a packet or decoder.
decode_fh4_packet = decode_packet
FH4Packet = FH4TelemetryPacket
FH4Telemetry = FH4TelemetryPacket
PacketDecodeError = TelemetryDecodeError

__all__ = [
    "FH4Packet",
    "FH4Telemetry",
    "FH4TelemetryPacket",
    "PACKET_LENGTHS",
    "PACKET_SIZE",
    "PACKET_SIZE_WITH_TRAILING_BYTE",
    "NormalizedControls",
    "PacketDecodeError",
    "TelemetryDecodeError",
    "decode_fh4_packet",
    "decode_packet",
]
