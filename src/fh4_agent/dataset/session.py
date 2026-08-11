"""Strict, bounded-memory adapter for synchronized recording sessions."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from ..capture.camera import CameraProfile, _validate_jpeg
from ..capture.recording import (
    MAX_JSON_LINE_BYTES,
    read_frame_chunks,
    validate_manifest,
)
from ..capture.telemetry import CaptureReader, CaptureRecord
from ..contracts import BenchmarkIdentity, ControllerSample, SessionMetadata
from ..telemetry import FH4TelemetryPacket, TimestampContinuity, decode_packet
from .contracts import (
    DEFAULT_ALIGNMENT_NS,
    MAX_MISALIGNED_FRACTION,
    AlignedFrame,
    DatasetError,
    LapCandidate,
    SessionQuality,
    validate_session_id,
)
from .laps import LapStateMachine

_FATAL_HEALTH = ("stale", "disconnects", "writer_faults", "overload_failures")
_MAX_LAP_CANDIDATES = 1_024
_MAX_RACE_SEGMENTS = 1_024
_MIN_RACE_SEGMENT_FRACTION = 0.02


@dataclass(frozen=True, slots=True)
class _ControllerRecord:
    session_ns: int
    sample: ControllerSample


@dataclass(frozen=True, slots=True)
class _TelemetryRecord:
    session_ns: int
    source_clock_ns: int
    packet: FH4TelemetryPacket


class _BoundedDeltaStats:
    """Deterministic fixed-memory sample with an exact count and maximum."""

    _LIMIT = 4_096

    def __init__(self) -> None:
        self.count = 0
        self.maximum: int | None = None
        self._sample: list[int] = []
        self._state = 0xA5A5A5A5

    def add(self, delta_ns: int | None) -> None:
        if delta_ns is None:
            return
        value = abs(delta_ns)
        self.count += 1
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        if len(self._sample) < self._LIMIT:
            self._sample.append(value)
            return
        self._state = (1_664_525 * self._state + 1_013_904_223) & 0xFFFFFFFF
        index = self._state % self.count
        if index < self._LIMIT:
            self._sample[index] = value

    def _percentile(self, quantile: float) -> float | None:
        if not self._sample:
            return None
        ordered = sorted(self._sample)
        position = (len(ordered) - 1) * quantile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    def summary(self) -> dict[str, int | float | bool | None]:
        return {
            "count": self.count,
            "sample_size": len(self._sample),
            "percentiles_exact": self.count <= self._LIMIT,
            "p50_abs_delta_ns": self._percentile(0.50),
            "p95_abs_delta_ns": self._percentile(0.95),
            "p99_abs_delta_ns": self._percentile(0.99),
            "max_abs_delta_ns": self.maximum,
        }


class _NearestCursor[T]:
    def __init__(self, values: Iterator[T], stamp: Callable[[T], int]) -> None:
        self._values = values
        self._stamp = stamp
        self.previous: T | None = None
        self.current = next(values, None)

    def nearest(self, target_ns: int) -> tuple[T, int] | None:
        while self.current is not None and self._stamp(self.current) <= target_ns:
            self.previous = self.current
            self.current = next(self._values, None)
        choices = [
            value for value in (self.previous, self.current) if value is not None
        ]
        if not choices:
            return None
        best = min(
            choices,
            key=lambda value: (abs(self._stamp(value) - target_ns), self._stamp(value)),
        )
        return best, self._stamp(best) - target_ns

    def drain(self) -> None:
        for value in self._values:
            self.previous = value
        self.current = None


def _json_objects(path: Path, stream: str) -> Iterator[dict[str, object]]:
    try:
        with path.open("rb") as file:
            for raw in file:
                if len(raw) > MAX_JSON_LINE_BYTES or not raw.endswith(b"\n"):
                    raise DatasetError(
                        f"{stream} JSONL record is truncated or oversized"
                    )
                try:
                    value = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DatasetError(f"{stream} JSONL record is malformed") from exc
                if not isinstance(value, dict):
                    raise DatasetError(f"{stream} JSONL record must be an object")
                yield value
    except OSError as exc:
        raise DatasetError(f"cannot read {stream} stream") from exc


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DatasetError(f"{field} must be a non-negative integer")
    return value


class StreamingSessionAdapter:
    """Validate and align one immutable recording without loading its streams."""

    def __init__(
        self,
        directory: str | Path,
        benchmark: BenchmarkIdentity,
        profile: CameraProfile | None = None,
        *,
        max_alignment_ns: int = DEFAULT_ALIGNMENT_NS,
    ) -> None:
        if (
            isinstance(max_alignment_ns, bool)
            or not isinstance(max_alignment_ns, int)
            or max_alignment_ns < 0
        ):
            raise ValueError("max_alignment_ns must be a non-negative integer")
        self.directory = Path(directory)
        self.benchmark = benchmark
        self.profile = profile or CameraProfile()
        self.max_alignment_ns = max_alignment_ns
        self.manifest = validate_manifest(
            self.directory / "manifest.json", self.benchmark, self.profile
        )
        if (
            self.manifest.metadata is None
        ):  # defensive; strict manifest already rejects it
            raise DatasetError("session metadata is missing")
        self.metadata = SessionMetadata.from_mapping(self.manifest.metadata)
        validate_session_id(self.metadata.session_id)
        self._quality: SessionQuality | None = None

    def _controllers(
        self, on_value: Callable[[_ControllerRecord], None]
    ) -> Iterator[_ControllerRecord]:
        last_session: int | None = None
        last_source: int | None = None
        for value in _json_objects(self.directory / "controller.jsonl", "controller"):
            if set(value) != {"session_ns", "sample"} or not isinstance(
                value["sample"], dict
            ):
                raise DatasetError("controller record schema is invalid")
            session_ns = _integer(value["session_ns"], "controller.session_ns")
            try:
                sample = ControllerSample.from_mapping(value["sample"])
            except (TypeError, ValueError) as exc:
                raise DatasetError("controller sample schema is invalid") from exc
            if sample.session_ns != session_ns:
                raise DatasetError("controller wrapper and sample clocks differ")
            source_ns = _integer(sample.source_clock_ns, "controller.source_clock_ns")
            if not sample.connected or not sample.is_physical:
                raise DatasetError(
                    "controller stream contains disconnected or non-physical input"
                )
            if last_session is not None and session_ns < last_session:
                raise DatasetError("controller session clock decreases")
            if last_source is not None and source_ns < last_source:
                raise DatasetError("controller source clock decreases")
            last_session, last_source = session_ns, source_ns
            record = _ControllerRecord(session_ns, sample)
            on_value(record)
            yield record

    def _telemetry(
        self, on_value: Callable[[_TelemetryRecord], None]
    ) -> Iterator[_TelemetryRecord]:
        sidecars = _json_objects(self.directory / "telemetry.jsonl", "telemetry")
        binaries = iter(CaptureReader(self.directory / "telemetry.fh4cap"))
        sentinel = object()
        last_session: int | None = None
        last_source: int | None = None
        for binary, sidecar in zip_longest(binaries, sidecars, fillvalue=sentinel):
            if binary is sentinel or sidecar is sentinel:
                raise DatasetError("binary and sidecar telemetry record counts differ")
            assert isinstance(binary, CaptureRecord) and isinstance(sidecar, dict)
            if set(sidecar) != {
                "session_ns",
                "source_clock_ns",
                "game_clock_ms",
                "source",
                "payload_hex",
            }:
                raise DatasetError("telemetry sidecar schema is invalid")
            session_ns = _integer(sidecar["session_ns"], "telemetry.session_ns")
            source_ns = _integer(
                sidecar["source_clock_ns"], "telemetry.source_clock_ns"
            )
            game_ms = _integer(sidecar["game_clock_ms"], "telemetry.game_clock_ms")
            source = sidecar["source"]
            if (
                not isinstance(source, list)
                or len(source) != 2
                or not isinstance(source[0], str)
                or isinstance(source[1], bool)
                or not isinstance(source[1], int)
            ):
                raise DatasetError("telemetry source schema is invalid")
            payload_hex = sidecar["payload_hex"]
            if not isinstance(payload_hex, str):
                raise DatasetError("telemetry payload_hex must be a string")
            try:
                payload = bytes.fromhex(payload_hex)
            except ValueError as exc:
                raise DatasetError("telemetry payload_hex is invalid") from exc
            if (
                binary.session_ns != session_ns
                or binary.source != (source[0], source[1])
                or binary.payload != payload
            ):
                raise DatasetError("binary and sidecar telemetry records differ")
            try:
                packet = decode_packet(payload)
            except ValueError as exc:
                raise DatasetError("telemetry packet is invalid") from exc
            if packet.timestamp_ms != game_ms:
                raise DatasetError("telemetry game clock differs from packet")
            if last_session is not None and session_ns < last_session:
                raise DatasetError("telemetry session clock decreases")
            if last_source is not None and source_ns < last_source:
                raise DatasetError("telemetry source clock decreases")
            last_session, last_source = session_ns, source_ns
            record = _TelemetryRecord(session_ns, source_ns, packet)
            on_value(record)
            yield record

    def _aligned_pass(
        self,
        on_controller: Callable[[_ControllerRecord], None],
        on_telemetry: Callable[[_TelemetryRecord], None],
        on_frame: Callable[[int], None],
        on_alignment: Callable[[int | None, int | None], None],
    ) -> Iterator[AlignedFrame | None]:
        controllers = _NearestCursor(
            self._controllers(on_controller), lambda value: value.session_ns
        )
        telemetry = _NearestCursor(
            self._telemetry(on_telemetry), lambda value: value.session_ns
        )
        frame_count = 0
        for frame_count, frame in enumerate(
            read_frame_chunks(self.directory / "frames.fh4jpg"), start=1
        ):
            on_frame(frame.session_ns)
            try:
                jpeg = _validate_jpeg(frame.jpeg, self.profile)
            except ValueError as exc:
                raise DatasetError("frame JPEG is invalid") from exc
            controller_match = controllers.nearest(frame.session_ns)
            telemetry_match = telemetry.nearest(frame.session_ns)
            on_alignment(
                controller_match[1] if controller_match is not None else None,
                telemetry_match[1] if telemetry_match is not None else None,
            )
            if (
                controller_match is None
                or telemetry_match is None
                or abs(controller_match[1]) > self.max_alignment_ns
                or abs(telemetry_match[1]) > self.max_alignment_ns
            ):
                yield None
                continue
            controller, controller_delta = controller_match
            telemetry_value, telemetry_delta = telemetry_match
            yield AlignedFrame(
                self.metadata.session_id,
                frame_count - 1,
                frame.session_ns,
                frame.source_clock_ns,
                jpeg,
                controller.sample.action,
                telemetry_value.packet,
                controller_delta,
                telemetry_delta,
            )
        controllers.drain()
        telemetry.drain()

    def validate(self) -> SessionQuality:
        if self._quality is not None:
            return self._quality
        controller_count = telemetry_count = 0
        min_clock: int | None = None
        max_clock: int | None = None
        stream_first: dict[str, int | None] = {
            "frames": None,
            "controller": None,
            "telemetry": None,
        }
        stream_last = dict(stream_first)
        control_names = ("steering", "throttle", "brake", "handbrake")
        action_sums = {name: 0.0 for name in control_names}
        action_squares = {name: 0.0 for name in control_names}
        action_min = {name: float("inf") for name in control_names}
        action_max = {name: float("-inf") for name in control_names}
        action_nonzero = {name: 0 for name in control_names}
        action_count = 0
        controller_alignment = _BoundedDeltaStats()
        telemetry_alignment = _BoundedDeltaStats()
        raw_race_segments: list[
            tuple[int, int, dict[str, int], tuple[LapCandidate, ...]]
        ] = []
        active_race_start_ns: int | None = None
        active_continuity: TimestampContinuity | None = None
        active_laps: LapStateMachine | None = None
        active_candidates: list[LapCandidate] = []
        candidate_count = 0
        last_telemetry_ns: int | None = None
        current_frame_ns = 0
        current_controller_delta: int | None = None
        current_telemetry_delta: int | None = None

        def stream_clock_seen(stream: str, session_ns: int) -> None:
            nonlocal min_clock, max_clock
            min_clock = session_ns if min_clock is None else min(min_clock, session_ns)
            max_clock = session_ns if max_clock is None else max(max_clock, session_ns)
            if stream_first[stream] is None:
                stream_first[stream] = session_ns
            stream_last[stream] = session_ns

        def controller_seen(value: _ControllerRecord) -> None:
            nonlocal controller_count
            controller_count += 1
            stream_clock_seen("controller", value.session_ns)

        def finish_race_segment(end_ns: int) -> None:
            nonlocal active_race_start_ns, active_continuity, active_laps
            nonlocal active_candidates
            if active_race_start_ns is None or active_continuity is None:
                return
            if len(raw_race_segments) >= _MAX_RACE_SEGMENTS:
                raise DatasetError("too many race-state segments")
            raw_race_segments.append(
                (
                    active_race_start_ns,
                    end_ns,
                    active_continuity.diagnostics(),
                    tuple(active_candidates),
                )
            )
            active_race_start_ns = None
            active_continuity = None
            active_laps = None
            active_candidates = []

        def telemetry_seen(value: _TelemetryRecord) -> None:
            nonlocal telemetry_count, candidate_count, last_telemetry_ns
            nonlocal active_race_start_ns, active_continuity, active_laps
            telemetry_count += 1
            last_telemetry_ns = value.session_ns
            stream_clock_seen("telemetry", value.session_ns)
            if not value.packet.is_race_on:
                finish_race_segment(value.session_ns)
                return
            if active_race_start_ns is None:
                active_race_start_ns = value.session_ns
                active_continuity = TimestampContinuity()
                active_laps = LapStateMachine(self.metadata.session_id)
            assert active_continuity is not None and active_laps is not None
            active_continuity.update(value.packet.timestamp_ms)
            candidate = active_laps.update(value.session_ns, value.packet)
            if candidate is not None:
                if candidate_count >= _MAX_LAP_CANDIDATES:
                    raise DatasetError("too many candidate laps")
                active_candidates.append(candidate)
                candidate_count += 1

        for _value in self._telemetry(telemetry_seen):
            pass
        if active_race_start_ns is not None and last_telemetry_ns is not None:
            finish_race_segment(last_telemetry_ns + self.max_alignment_ns + 1)

        telemetry_first = stream_first["telemetry"]
        telemetry_last = stream_last["telemetry"]
        telemetry_span_ns = (
            telemetry_last - telemetry_first
            if telemetry_first is not None and telemetry_last is not None
            else 0
        )
        minimum_segment_ns = int(
            telemetry_span_ns * _MIN_RACE_SEGMENT_FRACTION
        )
        selected_race_segments = [
            segment
            for segment in raw_race_segments
            if segment[1] - segment[0] >= minimum_segment_ns
        ]
        race_segments = tuple(
            (start_ns, end_ns)
            for start_ns, end_ns, _diagnostics, _candidates in selected_race_segments
        )
        continuity_diagnostics = {
            key: sum(segment[2][key] for segment in selected_race_segments)
            for key in (
                "packets",
                "duplicates",
                "out_of_order",
                "wraps",
                "estimated_missing_packets",
            )
        }
        candidates = [
            candidate
            for segment in selected_race_segments
            for candidate in segment[3]
        ]

        def frame_seen(session_ns: int) -> None:
            nonlocal current_frame_ns
            current_frame_ns = session_ns
            stream_clock_seen("frames", session_ns)

        def alignment_seen(
            controller_delta: int | None, telemetry_delta: int | None
        ) -> None:
            nonlocal current_controller_delta, current_telemetry_delta
            current_controller_delta = controller_delta
            current_telemetry_delta = telemetry_delta

        frame_count = aligned = rejected = excluded_non_race = 0
        for value in self._aligned_pass(
            controller_seen,
            lambda _value: None,
            frame_seen,
            alignment_seen,
        ):
            frame_count += 1
            in_selected_race = any(
                current_frame_ns + self.max_alignment_ns >= start_ns
                and current_frame_ns < end_ns
                for start_ns, end_ns in race_segments
            )
            if not in_selected_race:
                excluded_non_race += 1
                continue
            controller_alignment.add(current_controller_delta)
            telemetry_alignment.add(current_telemetry_delta)
            if value is None or not value.telemetry.is_race_on:
                rejected += 1
                continue
            aligned += 1
            action_count += 1
            for name, number in value.action.to_mapping().items():
                action_sums[name] += number
                action_squares[name] += number * number
                action_min[name] = min(action_min[name], number)
                action_max[name] = max(action_max[name], number)
                action_nonzero[name] += int(number != 0.0)
        reasons = [
            f"health.{field}={self.manifest.health[field]}"
            for field in _FATAL_HEALTH
            if self.manifest.health[field] > 0
        ]
        if not race_segments:
            reasons.append("no substantial race segment")
        if aligned == 0:
            reasons.append("no aligned race frames")
        eligible_race_frames = aligned + rejected
        if (
            eligible_race_frames
            and rejected / eligible_race_frames > MAX_MISALIGNED_FRACTION
        ):
            reasons.append("more than 1% of race frames are misaligned")
        if continuity_diagnostics["out_of_order"]:
            reasons.append(
                "telemetry game clock has out-of-order timestamps: "
                f"{continuity_diagnostics['out_of_order']}"
            )
        if continuity_diagnostics["estimated_missing_packets"]:
            reasons.append(
                "telemetry game clock has estimated missing timestamps: "
                f"{continuity_diagnostics['estimated_missing_packets']}"
            )
        warnings = []
        excluded_short_segments = len(raw_race_segments) - len(race_segments)
        if excluded_short_segments:
            warnings.append(
                f"excluded short race segments: {excluded_short_segments}"
            )
        if excluded_non_race:
            warnings.append(f"excluded non-race frames: {excluded_non_race}")
        if self.manifest.health["dropped"]:
            warnings.append(
                f"capture drops reported: {self.manifest.health['dropped']}"
            )
        if (
            self.metadata.game_build.strip().lower() in {"unknown", "unverified"}
            or "unverified" in self.metadata.game_build.lower()
        ):
            warnings.append("game build is unverified")
        controls: dict[str, float] = {}
        for name in control_names:
            if action_count:
                controls[f"{name}_mean"] = action_sums[name] / action_count
                controls[f"{name}_min"] = action_min[name]
                controls[f"{name}_max"] = action_max[name]
                controls[f"{name}_max_abs"] = max(
                    abs(action_min[name]), abs(action_max[name])
                )
                controls[f"{name}_rms"] = (
                    action_squares[name] / action_count
                ) ** 0.5
                controls[f"{name}_nonzero_fraction"] = (
                    action_nonzero[name] / action_count
                )
            else:
                for statistic in (
                    "mean",
                    "min",
                    "max",
                    "max_abs",
                    "rms",
                    "nonzero_fraction",
                ):
                    controls[f"{name}_{statistic}"] = 0.0
        duration_ns = (
            max_clock - min_clock
            if min_clock is not None and max_clock is not None
            else 0
        )
        stream_clocks: dict[str, dict[str, int | None]] = {}
        for stream in ("frames", "controller", "telemetry"):
            first = stream_first[stream]
            last = stream_last[stream]
            stream_clocks[stream] = {
                "first_session_ns": first,
                "last_session_ns": last,
                "span_ns": (
                    last - first if first is not None and last is not None else None
                ),
            }
        alignment = {
            "controller": controller_alignment.summary(),
            "telemetry": telemetry_alignment.summary(),
        }
        self._quality = SessionQuality(
            self.metadata.session_id,
            not reasons,
            tuple(reasons),
            tuple(warnings),
            self.manifest.benchmark_digest,
            self.manifest.profile_digest,
            self.metadata.config_digest,
            self.metadata.game_build,
            frame_count,
            controller_count,
            telemetry_count,
            aligned,
            rejected,
            excluded_non_race,
            race_segments,
            duration_ns,
            stream_clocks,
            alignment,
            continuity_diagnostics,
            self.manifest.health,
            controls,
            tuple(candidates),
        )
        return self._quality

    def aligned_frames(self) -> Iterator[AlignedFrame]:
        quality = self.validate()
        if not quality.accepted:
            raise DatasetError(
                f"session {quality.session_id} rejected: " + "; ".join(quality.reasons)
            )
        for value in self._aligned_pass(
            lambda _value: None,
            lambda _value: None,
            lambda _value: None,
            lambda _controller, _telemetry: None,
        ):
            if value is not None and value.telemetry.is_race_on and any(
                value.session_ns + self.max_alignment_ns >= start_ns
                and value.session_ns < end_ns
                for start_ns, end_ns in quality.race_segments
            ):
                yield value


__all__ = ["StreamingSessionAdapter"]
