"""Small, dependency-free typed contracts shared across FH4 modules.

The contracts deliberately contain no game or device integration.  Values are
validated at construction boundaries so malformed samples and actions fail
close to their source rather than being silently propagated.
"""
# ruff: noqa: E501

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self


class ContractError(ValueError):
    """Base error for malformed public contract values."""


class ActionValidationError(ContractError):
    """Raised when a requested control action is malformed or out of range."""


class BenchmarkValidationError(ContractError):
    """Raised when benchmark identity/configuration is malformed."""


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{context} must be an object")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value.strip()


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{field} must be a finite number")
    return result


def _timestamp(value: object) -> float:
    result = _finite_number(value, "timestamp")
    if result < 0:
        raise ContractError("timestamp must be non-negative")
    return result


def _bounded(value: object, field: str, lower: float, upper: float) -> float:
    result = _finite_number(value, field)
    if not lower <= result <= upper:
        raise ContractError(f"{field} must be between {lower} and {upper}")
    return result


def _check_keys(values: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(values) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ContractError(f"{context} contains unknown field(s): {names}")


@dataclass(frozen=True, slots=True)
class AssistConfiguration:
    """Fixed in-game assists that identify a benchmark."""

    racing_line: str
    abs_enabled: bool
    traction_control: bool
    transmission: str
    steering: str
    assisted_braking: bool

    def __post_init__(self) -> None:
        _string(self.racing_line, "assists.racing_line")
        _string(self.transmission, "assists.transmission")
        _string(self.steering, "assists.steering")
        if self.racing_line != "full":
            raise BenchmarkValidationError("assists.racing_line must be 'full'")
        if self.transmission != "automatic":
            raise BenchmarkValidationError("assists.transmission must be 'automatic'")
        if self.steering != "normal":
            raise BenchmarkValidationError("assists.steering must be 'normal'")
        for field in ("abs_enabled", "traction_control", "assisted_braking"):
            if not isinstance(getattr(self, field), bool):
                raise BenchmarkValidationError(f"assists.{field} must be boolean")

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "assists")
        _check_keys(
            values,
            {
                "racing_line",
                "abs",
                "traction_control",
                "transmission",
                "steering",
                "assisted_braking",
            },
            "assists",
        )
        required = {
            "racing_line",
            "abs",
            "traction_control",
            "transmission",
            "steering",
            "assisted_braking",
        }
        missing = required - set(values)
        if missing:
            raise BenchmarkValidationError(
                f"assists is missing required field(s): {', '.join(sorted(missing))}"
            )
        booleans: dict[str, bool] = {}
        for field in ("abs", "traction_control", "assisted_braking"):
            item = values[field]
            if not isinstance(item, bool):
                raise BenchmarkValidationError(f"assists.{field} must be boolean")
            booleans[field] = item
        racing_line = _string(values["racing_line"], "assists.racing_line")
        transmission = _string(values["transmission"], "assists.transmission")
        steering = _string(values["steering"], "assists.steering")
        return cls(
            racing_line=racing_line,
            abs_enabled=booleans["abs"],
            traction_control=booleans["traction_control"],
            transmission=transmission,
            steering=steering,
            assisted_braking=booleans["assisted_braking"],
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "racing_line": self.racing_line,
            "abs": self.abs_enabled,
            "traction_control": self.traction_control,
            "transmission": self.transmission,
            "steering": self.steering,
            "assisted_braking": self.assisted_braking,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkIdentity:
    """Identity of the game setup associated with recorded data."""

    track: str
    car_make: str
    car_model: str
    car_year: int
    car_condition: str
    season: str
    weather: str
    time_of_day: str
    camera: str
    assists: AssistConfiguration

    def __post_init__(self) -> None:
        for field in (
            "track",
            "car_make",
            "car_model",
            "car_condition",
            "season",
            "weather",
            "time_of_day",
            "camera",
        ):
            _string(getattr(self, field), f"benchmark.{field}")
        if isinstance(self.car_year, bool) or not isinstance(self.car_year, int):
            raise BenchmarkValidationError("benchmark.car_year must be a valid year")
        if self.car_year < 1886:
            raise BenchmarkValidationError("benchmark.car_year must be a valid year")
        if not isinstance(self.assists, AssistConfiguration):
            raise BenchmarkValidationError(
                "benchmark.assists must be AssistConfiguration"
            )

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "benchmark")
        allowed = {
            "track",
            "car_make",
            "car_model",
            "car_year",
            "car_condition",
            "season",
            "weather",
            "time_of_day",
            "camera",
            "assists",
        }
        _check_keys(values, allowed, "benchmark")
        missing = allowed - set(values)
        if missing:
            raise BenchmarkValidationError(
                "benchmark is missing required field(s): " + ", ".join(sorted(missing))
            )
        year = values["car_year"]
        if isinstance(year, bool) or not isinstance(year, int) or year < 1886:
            raise BenchmarkValidationError("benchmark.car_year must be a valid year")
        result = cls(
            track=_string(values["track"], "benchmark.track"),
            car_make=_string(values["car_make"], "benchmark.car_make"),
            car_model=_string(values["car_model"], "benchmark.car_model"),
            car_year=year,
            car_condition=_string(values["car_condition"], "benchmark.car_condition"),
            season=_string(values["season"], "benchmark.season"),
            weather=_string(values["weather"], "benchmark.weather"),
            time_of_day=_string(values["time_of_day"], "benchmark.time_of_day"),
            camera=_string(values["camera"], "benchmark.camera"),
            assists=AssistConfiguration.from_mapping(values["assists"]),
        )
        return result

    @property
    def car(self) -> str:
        """Human-readable vehicle identity."""
        return (
            f"{self.car_year} {self.car_make} {self.car_model} ({self.car_condition})"
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "track": self.track,
            "car_make": self.car_make,
            "car_model": self.car_model,
            "car_year": self.car_year,
            "car_condition": self.car_condition,
            "season": self.season,
            "weather": self.weather,
            "time_of_day": self.time_of_day,
            "camera": self.camera,
            "assists": self.assists.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Versioned benchmark configuration loaded from a file."""

    identity: BenchmarkIdentity
    config_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.identity, BenchmarkIdentity):
            raise BenchmarkValidationError("identity must be BenchmarkIdentity")
        if isinstance(self.config_version, bool) or self.config_version != 1:
            raise BenchmarkValidationError("config_version must be 1")

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "config")
        _check_keys(values, {"config_version", "benchmark"}, "config")
        if "benchmark" not in values:
            raise BenchmarkValidationError(
                "config is missing required field: benchmark"
            )
        version = values.get("config_version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise BenchmarkValidationError("config_version must be 1")
        return cls(
            identity=BenchmarkIdentity.from_mapping(values["benchmark"]),
            config_version=version,
        )

    @property
    def track(self) -> str:
        return self.identity.track

    @property
    def car(self) -> str:
        return self.identity.car

    def to_mapping(self) -> dict[str, object]:
        return {
            "config_version": self.config_version,
            "benchmark": self.identity.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class RequestedControlAction:
    """Normalized control request; no field may exceed its physical range."""

    steering: float
    throttle: float
    brake: float
    handbrake: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "steering", _bounded(self.steering, "steering", -1.0, 1.0)
        )
        object.__setattr__(
            self, "throttle", _bounded(self.throttle, "throttle", 0.0, 1.0)
        )
        object.__setattr__(self, "brake", _bounded(self.brake, "brake", 0.0, 1.0))
        object.__setattr__(
            self, "handbrake", _bounded(self.handbrake, "handbrake", 0.0, 1.0)
        )

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        try:
            values = _require_mapping(value, "action")
            _check_keys(
                values, {"steering", "throttle", "brake", "handbrake"}, "action"
            )
            required = {"steering", "throttle", "brake"}
            missing = required - set(values)
            if missing:
                raise ActionValidationError(
                    "action is missing required field(s): " + ", ".join(sorted(missing))
                )
            return cls(
                steering=values["steering"],
                throttle=values["throttle"],
                brake=values["brake"],
                handbrake=values.get("handbrake", 0.0),
            )
        except ContractError as exc:
            if isinstance(exc, ActionValidationError):
                raise
            raise ActionValidationError(str(exc)) from exc

    def to_mapping(self) -> dict[str, float]:
        return {
            "steering": self.steering,
            "throttle": self.throttle,
            "brake": self.brake,
            "handbrake": self.handbrake,
        }


def neutral_action() -> RequestedControlAction:
    """Return the fail-safe action that releases every control."""
    return RequestedControlAction(steering=0.0, throttle=0.0, brake=0.0, handbrake=0.0)


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    """A minimal timestamped ego-telemetry sample."""

    timestamp: float
    speed_mps: float = 0.0
    distance_m: float = 0.0
    engine_rpm: float = 0.0
    lap_number: int = 0
    is_race_on: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        object.__setattr__(
            self, "speed_mps", _bounded(self.speed_mps, "speed_mps", 0, 200)
        )
        object.__setattr__(
            self, "distance_m", _bounded(self.distance_m, "distance_m", 0, 1e9)
        )
        object.__setattr__(
            self, "engine_rpm", _bounded(self.engine_rpm, "engine_rpm", 0, 1e6)
        )
        if (
            isinstance(self.lap_number, bool)
            or not isinstance(self.lap_number, int)
            or self.lap_number < 0
        ):
            raise ContractError("lap_number must be a non-negative integer")
        if not isinstance(self.is_race_on, bool):
            raise ContractError("is_race_on must be boolean")

    def to_mapping(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "speed_mps": self.speed_mps,
            "distance_m": self.distance_m,
            "engine_rpm": self.engine_rpm,
            "lap_number": self.lap_number,
            "is_race_on": self.is_race_on,
        }

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "telemetry sample")
        _check_keys(values, set(cls.__dataclass_fields__), "telemetry sample")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class FrameSample:
    """A timestamped frame reference; pixel storage belongs to capture modules."""

    timestamp: float
    frame_id: int
    width: int
    height: int
    source: str = "hood-camera"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        for value, field in (
            (self.frame_id, "frame_id"),
            (self.width, "width"),
            (self.height, "height"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(f"{field} must be a non-negative integer")
        if self.width == 0 or self.height == 0:
            raise ContractError("frame width and height must be positive")
        _string(self.source, "source")

    def to_mapping(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "frame_id": self.frame_id,
            "width": self.width,
            "height": self.height,
            "source": self.source,
        }

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "frame sample")
        _check_keys(values, set(cls.__dataclass_fields__), "frame sample")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ControllerSample:
    """A timestamped physical-controller observation with optional raw state."""

    timestamp: float
    action: RequestedControlAction
    is_physical: bool = True
    session_ns: int | None = None
    source_clock_ns: int | None = None
    packet: int | None = None
    raw_state: Mapping[str, int] | None = None
    connected: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        for value, field in (
            (self.session_ns, "session_ns"),
            (self.source_clock_ns, "source_clock_ns"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ContractError(f"{field} must be a non-negative integer")
        if not isinstance(self.action, RequestedControlAction):
            raise ContractError(
                "controller sample action must be RequestedControlAction"
            )
        if not isinstance(self.is_physical, bool):
            raise ContractError("is_physical must be boolean")
        if self.packet is not None and (
            isinstance(self.packet, bool)
            or not isinstance(self.packet, int)
            or self.packet < 0
        ):
            raise ContractError(
                "controller sample packet must be a non-negative integer"
            )
        if self.raw_state is not None and (
            not isinstance(self.raw_state, Mapping)
            or any(
                not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int)
                for k, v in self.raw_state.items()
            )
        ):
            raise ContractError(
                "controller sample raw_state must map strings to integers"
            )
        if not isinstance(self.connected, bool):
            raise ContractError("connected must be boolean")

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "timestamp": self.timestamp,
            "action": self.action.to_mapping(),
            "is_physical": self.is_physical,
        }
        if self.session_ns is not None:
            result["session_ns"] = self.session_ns
        if self.source_clock_ns is not None:
            result["source_clock_ns"] = self.source_clock_ns
        if self.packet is not None:
            result["packet"] = self.packet
        if self.raw_state is not None:
            result["raw_state"] = dict(self.raw_state)
        result["connected"] = self.connected
        return result

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "controller sample")
        _check_keys(values, set(cls.__dataclass_fields__), "controller sample")
        if "timestamp" not in values or "action" not in values:
            raise ContractError("controller sample requires timestamp and action")
        return cls(
            timestamp=values["timestamp"],
            action=RequestedControlAction.from_mapping(values["action"]),
            is_physical=values.get("is_physical", True),
            session_ns=values.get("session_ns"),
            source_clock_ns=values.get("source_clock_ns"),
            packet=values.get("packet"),
            raw_state=values.get("raw_state"),
            connected=values.get("connected", True),
        )


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Metadata binding all recorded samples to one benchmark identity."""

    session_id: str
    benchmark: BenchmarkIdentity
    started_at_utc: str
    started_monotonic_s: float
    game_build: str
    config_digest: str
    profile_digest: str = ""
    controller_slot: int | None = None

    def __post_init__(self) -> None:
        _string(self.session_id, "session_id")
        if not isinstance(self.benchmark, BenchmarkIdentity):
            raise ContractError("benchmark must be BenchmarkIdentity")
        _string(self.started_at_utc, "started_at_utc")
        _timestamp(self.started_monotonic_s)
        _string(self.game_build, "game_build")
        _string(self.config_digest, "config_digest")
        if not isinstance(self.profile_digest, str):
            raise ContractError("profile_digest must be a string")
        if self.controller_slot is not None and (
            isinstance(self.controller_slot, bool)
            or not isinstance(self.controller_slot, int)
            or not 0 <= self.controller_slot <= 3
        ):
            raise ContractError("controller_slot must be an integer from 0 through 3")

    @classmethod
    def create(
        cls,
        benchmark: BenchmarkIdentity,
        *,
        game_build: str,
        started_monotonic_s: float,
        session_id: str | None = None,
        profile_digest: str = "",
        controller_slot: int | None = None,
    ) -> Self:
        if not isinstance(benchmark, BenchmarkIdentity):
            raise ContractError("benchmark must be BenchmarkIdentity")
        return cls(
            session_id=session_id or str(uuid.uuid4()),
            benchmark=benchmark,
            started_at_utc=datetime.now(UTC).isoformat(),
            started_monotonic_s=started_monotonic_s,
            game_build=game_build,
            config_digest=benchmark_digest(benchmark),
            profile_digest=profile_digest,
            controller_slot=controller_slot,
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "session_id": self.session_id,
            "benchmark": self.benchmark.to_mapping(),
            "started_at_utc": self.started_at_utc,
            "started_monotonic_s": self.started_monotonic_s,
            "game_build": self.game_build,
            "config_digest": self.config_digest,
            "profile_digest": self.profile_digest,
        }
        if self.controller_slot is not None:
            result["controller_slot"] = self.controller_slot
        return result

    @classmethod
    def from_mapping(cls, value: object) -> Self:
        values = _require_mapping(value, "session metadata")
        allowed = {
            "session_id",
            "benchmark",
            "started_at_utc",
            "started_monotonic_s",
            "game_build",
            "config_digest",
            "profile_digest",
            "controller_slot",
        }
        _check_keys(values, allowed, "session metadata")
        required = allowed - {"profile_digest", "controller_slot"}
        missing = required - set(values)
        if missing:
            raise ContractError(
                "session metadata is missing required field(s): "
                + ", ".join(sorted(missing))
            )
        return cls(
            session_id=values["session_id"],
            benchmark=BenchmarkIdentity.from_mapping(values["benchmark"]),
            started_at_utc=values["started_at_utc"],
            started_monotonic_s=values["started_monotonic_s"],
            game_build=values["game_build"],
            config_digest=values["config_digest"],
            profile_digest=values.get("profile_digest", ""),
            controller_slot=values.get("controller_slot"),
        )


class ArmingState(StrEnum):
    """Runtime control state."""

    DISARMED = "disarmed"
    ARMED = "armed"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class RuntimeArmingState:
    """Explicit arming state bound to validated benchmark evidence.

    An armed state cannot be constructed without a :class:`BenchmarkConfig`.
    The evidence is retained on the state rather than represented by a caller
    supplied flag, so an action can only be emitted for the config that was
    actually loaded and validated.
    """

    state: ArmingState = ArmingState.DISARMED
    reason: str = "not explicitly armed"
    benchmark: BenchmarkConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ArmingState):
            raise ContractError("state must be ArmingState")
        _string(self.reason, "reason")
        if self.benchmark is not None and not isinstance(
            self.benchmark, BenchmarkConfig
        ):
            raise ContractError("benchmark must be BenchmarkConfig")
        if self.state is ArmingState.ARMED and self.benchmark is None:
            raise ContractError("armed state requires validated BenchmarkConfig")

    @classmethod
    def disarmed(cls, reason: str = "not explicitly armed") -> Self:
        return cls(ArmingState.DISARMED, reason, None)

    @classmethod
    def armed(cls, benchmark: BenchmarkConfig) -> Self:
        if not isinstance(benchmark, BenchmarkConfig):
            raise ContractError("armed state requires validated BenchmarkConfig")
        return cls(ArmingState.ARMED, "explicitly armed", benchmark)

    @classmethod
    def faulted(cls, reason: str) -> Self:
        return cls(ArmingState.FAULTED, reason, None)

    @property
    def benchmark_validated(self) -> bool:
        """Whether typed benchmark evidence is attached to this state."""
        return self.benchmark is not None

    @property
    def can_emit(self) -> bool:
        return self.state is ArmingState.ARMED and self.benchmark is not None


def benchmark_digest(benchmark: BenchmarkIdentity) -> str:
    """Return a stable digest suitable for session/config binding."""
    import hashlib
    import json

    if not isinstance(benchmark, BenchmarkIdentity):
        raise ContractError("benchmark must be BenchmarkIdentity")
    canonical = json.dumps(
        benchmark.to_mapping(), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
