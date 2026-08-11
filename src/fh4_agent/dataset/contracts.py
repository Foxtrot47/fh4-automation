"""Immutable contracts for offline session validation and dataset artifacts."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Literal

from ..contracts import RequestedControlAction
from ..telemetry import FH4TelemetryPacket

DEFAULT_ALIGNMENT_NS = 33_334_000
MAX_MISALIGNED_FRACTION = 0.01
MAX_SAMPLES_PER_SHARD = 1_024
SplitName = Literal["train", "validation", "test"]
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class DatasetError(ValueError):
    """A recording cannot safely be accepted or converted into a dataset."""


def validate_session_id(session_id: object) -> str:
    """Reject identifiers that are unsafe or too long for deterministic tar names."""
    if not isinstance(session_id, str) or _SESSION_ID.fullmatch(session_id) is None:
        raise DatasetError(
            "session ID must be 1-64 ASCII letters, digits, '.', '_', or '-' "
            "and start with a letter or digit"
        )
    return session_id


@dataclass(frozen=True, slots=True)
class LapCandidate:
    session_id: str
    lap_number: int
    start_session_ns: int
    end_session_ns: int
    duration_s: float
    collision_free: None = None


@dataclass(frozen=True, slots=True)
class AlignedFrame:
    session_id: str
    frame_index: int
    session_ns: int
    source_clock_ns: int
    jpeg: bytes
    action: RequestedControlAction
    telemetry: FH4TelemetryPacket
    controller_delta_ns: int
    telemetry_delta_ns: int

    def _telemetry_features(self) -> dict[str, object]:
        """Return reviewed ego state, never opaque bytes or applied controls."""
        packet = self.telemetry
        return {
            "is_race_on": packet.is_race_on,
            "game_timestamp_ms": packet.timestamp_ms,
            "engine_max_rpm": packet.engine_max_rpm,
            "engine_idle_rpm": packet.engine_idle_rpm,
            "current_engine_rpm": packet.current_engine_rpm,
            "acceleration_x": packet.acceleration_x,
            "acceleration_y": packet.acceleration_y,
            "acceleration_z": packet.acceleration_z,
            "velocity_x": packet.velocity_x,
            "velocity_y": packet.velocity_y,
            "velocity_z": packet.velocity_z,
            "angular_velocity_x": packet.angular_velocity_x,
            "angular_velocity_y": packet.angular_velocity_y,
            "angular_velocity_z": packet.angular_velocity_z,
            "yaw": packet.yaw,
            "pitch": packet.pitch,
            "roll": packet.roll,
            "position_x": packet.position_x,
            "position_y": packet.position_y,
            "position_z": packet.position_z,
            "speed": packet.speed,
            "power": packet.power,
            "torque": packet.torque,
            "boost": packet.boost,
            "fuel": packet.fuel,
            "distance_traveled": packet.distance_traveled,
            "best_lap": packet.best_lap,
            "last_lap": packet.last_lap,
            "current_lap": packet.current_lap,
            "current_race_time": packet.current_race_time,
            "lap_number": packet.lap_number,
            "race_position": packet.race_position,
            "gear": packet.gear,
            "wheel_rotation_speed_front_left": (packet.wheel_rotation_speed_front_left),
            "wheel_rotation_speed_front_right": (
                packet.wheel_rotation_speed_front_right
            ),
            "wheel_rotation_speed_rear_left": packet.wheel_rotation_speed_rear_left,
            "wheel_rotation_speed_rear_right": (packet.wheel_rotation_speed_rear_right),
            "tire_slip_ratio_front_left": packet.tire_slip_ratio_front_left,
            "tire_slip_ratio_front_right": packet.tire_slip_ratio_front_right,
            "tire_slip_ratio_rear_left": packet.tire_slip_ratio_rear_left,
            "tire_slip_ratio_rear_right": packet.tire_slip_ratio_rear_right,
            "tire_slip_angle_front_left": packet.tire_slip_angle_front_left,
            "tire_slip_angle_front_right": packet.tire_slip_angle_front_right,
            "tire_slip_angle_rear_left": packet.tire_slip_angle_rear_left,
            "tire_slip_angle_rear_right": packet.tire_slip_angle_rear_right,
            "tire_combined_slip_front_left": packet.tire_combined_slip_front_left,
            "tire_combined_slip_front_right": packet.tire_combined_slip_front_right,
            "tire_combined_slip_rear_left": packet.tire_combined_slip_rear_left,
            "tire_combined_slip_rear_right": packet.tire_combined_slip_rear_right,
            "normalized_suspension_travel_front_left": (
                packet.normalized_suspension_travel_front_left
            ),
            "normalized_suspension_travel_front_right": (
                packet.normalized_suspension_travel_front_right
            ),
            "normalized_suspension_travel_rear_left": (
                packet.normalized_suspension_travel_rear_left
            ),
            "normalized_suspension_travel_rear_right": (
                packet.normalized_suspension_travel_rear_right
            ),
            "suspension_travel_meters_front_left": (
                packet.suspension_travel_meters_front_left
            ),
            "suspension_travel_meters_front_right": (
                packet.suspension_travel_meters_front_right
            ),
            "suspension_travel_meters_rear_left": (
                packet.suspension_travel_meters_rear_left
            ),
            "suspension_travel_meters_rear_right": (
                packet.suspension_travel_meters_rear_right
            ),
            "tire_temp_front_left": packet.tire_temp_front_left,
            "tire_temp_front_right": packet.tire_temp_front_right,
            "tire_temp_rear_left": packet.tire_temp_rear_left,
            "tire_temp_rear_right": packet.tire_temp_rear_right,
        }

    def metadata(
        self, *, benchmark_digest: str, config_digest: str
    ) -> dict[str, object]:
        return {
            "schema": 1,
            "session_id": self.session_id,
            "benchmark_digest": benchmark_digest,
            "config_digest": config_digest,
            "frame_index": self.frame_index,
            "session_ns": self.session_ns,
            "source_clock_ns": self.source_clock_ns,
            "controller_delta_ns": self.controller_delta_ns,
            "telemetry_delta_ns": self.telemetry_delta_ns,
            "action": self.action.to_mapping(),
            "telemetry": self._telemetry_features(),
        }


@dataclass(frozen=True, slots=True)
class SessionQuality:
    session_id: str
    accepted: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    benchmark_digest: str
    profile_digest: str
    config_digest: str
    game_build: str
    frame_count: int
    controller_count: int
    telemetry_count: int
    aligned_frames: int
    alignment_rejections: int
    duration_ns: int
    stream_clocks: dict[str, dict[str, int | None]]
    alignment: dict[str, dict[str, int | float | bool | None]]
    telemetry_continuity: dict[str, int]
    health: dict[str, int]
    controls: dict[str, float]
    candidate_laps: tuple[LapCandidate, ...] = field(default_factory=tuple)

    @property
    def misaligned_fraction(self) -> float:
        return self.alignment_rejections / self.frame_count if self.frame_count else 1.0

    def to_mapping(self) -> dict[str, object]:
        duration_s = self.duration_ns / 1_000_000_000
        lap_durations = [lap.duration_s for lap in self.candidate_laps]

        def stream_rate(name: str, count: int) -> float:
            span = self.stream_clocks[name]["span_ns"]
            if count < 2 or span is None or span <= 0:
                return 0.0
            return (count - 1) * 1_000_000_000 / span

        rates = {
            "frames_hz": stream_rate("frames", self.frame_count),
            "controller_hz": stream_rate("controller", self.controller_count),
            "telemetry_hz": stream_rate("telemetry", self.telemetry_count),
        }
        return {
            "session_id": self.session_id,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "provenance": {
                "benchmark_digest": self.benchmark_digest,
                "profile_digest": self.profile_digest,
                "config_digest": self.config_digest,
                "game_build": self.game_build,
            },
            "counts": {
                "frames": self.frame_count,
                "controller": self.controller_count,
                "telemetry": self.telemetry_count,
                "aligned_frames": self.aligned_frames,
                "alignment_rejections": self.alignment_rejections,
            },
            "duration_ns": self.duration_ns,
            "duration_s": duration_s,
            "stream_clocks": self.stream_clocks,
            "rates": rates,
            "misaligned_fraction": self.misaligned_fraction,
            "alignment": self.alignment,
            "telemetry_continuity": self.telemetry_continuity,
            "controls": self.controls,
            "health": dict(sorted(self.health.items())),
            "candidate_lap_summary": {
                "count": len(lap_durations),
                "median_duration_s": (
                    statistics.median(lap_durations) if lap_durations else None
                ),
            },
            "candidate_complete_laps": [
                {
                    "lap_number": lap.lap_number,
                    "start_session_ns": lap.start_session_ns,
                    "end_session_ns": lap.end_session_ns,
                    "duration_s": lap.duration_s,
                    "collision_free": lap.collision_free,
                }
                for lap in self.candidate_laps
            ],
        }


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    train: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]

    def to_mapping(self) -> dict[str, list[str]]:
        return {
            "train": list(self.train),
            "validation": list(self.validation),
            "test": list(self.test),
        }


__all__ = [
    "AlignedFrame",
    "DEFAULT_ALIGNMENT_NS",
    "DatasetError",
    "LapCandidate",
    "MAX_MISALIGNED_FRACTION",
    "MAX_SAMPLES_PER_SHARD",
    "SessionQuality",
    "SplitAssignment",
    "SplitName",
    "validate_session_id",
]
