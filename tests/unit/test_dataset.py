from __future__ import annotations

import hashlib
import json
import struct
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from fh4_agent.capture import (
    CameraFrame,
    CameraProfile,
    SessionRecorder,
    profile_digest,
)
from fh4_agent.cli import main
from fh4_agent.contracts import (
    BenchmarkIdentity,
    ControllerSample,
    RequestedControlAction,
    SessionMetadata,
)
from fh4_agent.dataset import (
    DatasetError,
    LapStateMachine,
    StreamingSessionAdapter,
    assign_session_splits,
    build_dataset,
    quality_document,
)
from fh4_agent.sync import TimelineStamp
from fh4_agent.telemetry import PACKET_SIZE, decode_packet


def benchmark() -> BenchmarkIdentity:
    return BenchmarkIdentity.from_mapping(
        {
            "track": "Horizon Festival Circuit",
            "car_make": "Ford",
            "car_model": "Focus RS",
            "car_year": 2017,
            "car_condition": "stock",
            "season": "summer",
            "weather": "clear",
            "time_of_day": "daytime",
            "camera": "hood",
            "assists": {
                "racing_line": "full",
                "abs": True,
                "traction_control": True,
                "transmission": "automatic",
                "steering": "normal",
                "assisted_braking": False,
            },
        }
    )


def jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (960, 540), (20, 40, 60)).save(output, format="JPEG")
    return output.getvalue()


def packet(game_ms: int, lap: int = 1, race_s: float = 1.0, race: bool = True) -> bytes:
    raw = bytearray(PACKET_SIZE)
    struct.pack_into("<iI", raw, 0, int(race), game_ms)
    struct.pack_into("<f", raw, 308, race_s)
    struct.pack_into("<H", raw, 312, lap)
    return bytes(raw)


def make_session(
    root: Path,
    session_id: str,
    *,
    frame_times: tuple[int, ...] = (100,),
    health: str | None = None,
    controller_times: tuple[int, ...] = (100,),
    telemetry_points: tuple[
        tuple[int, int, int, float] | tuple[int, int, int, float, bool], ...
    ]
    | None = None,
) -> Path:
    profile = CameraProfile()
    metadata = SessionMetadata.create(
        benchmark(),
        game_build="unverified",
        started_monotonic_s=0,
        session_id=session_id,
        profile_digest=profile_digest(profile),
    )
    recorder = SessionRecorder(
        root,
        session_id=session_id,
        benchmark=benchmark(),
        profile=profile,
        metadata=metadata,
    )
    for index, stamp in enumerate(frame_times):
        recorder.append_frame(
            CameraFrame(
                index, TimelineStamp(stamp, stamp, source_clock_ns=stamp), jpeg()
            )
        )
    for session_ns in controller_times:
        recorder.append_controller(
            ControllerSample(
                0,
                RequestedControlAction(0.25, 0.5, 0.0),
                session_ns=session_ns,
                source_clock_ns=session_ns,
            )
        )
    points = telemetry_points or ((100, 10, 1, 1.0),)
    for point in points:
        if len(point) == 4:
            session_ns, game_ms, lap, race_s = point
            race = True
        else:
            session_ns, game_ms, lap, race_s, race = point
        recorder.append_telemetry(
            packet(game_ms, lap, race_s, race),
            session_ns,
            ("127.0.0.1", 5300),
            source_clock_ns=session_ns,
            game_clock_ms=game_ms,
        )
    if health == "stale":
        recorder.record_stale()
    elif health == "drop":
        recorder.record_drop()
    recorder.finalize()
    return root


def test_lap_state_machine_handles_wrap_duplicates_and_true_resets() -> None:
    machine = LapStateMachine("s")
    assert machine.update(0, decode_packet(packet(0xFFFFFFF0, 1, 1))) is None
    assert machine.update(10, decode_packet(packet(0xFFFFFFFA, 2, 10))) is None
    assert machine.update(11, decode_packet(packet(0xFFFFFFFA, 2, 10))) is None
    lap = machine.update(20, decode_packet(packet(5, 3, 20)))
    assert lap is not None and lap.lap_number == 2 and lap.duration_s == 10
    assert lap.collision_free is None

    # A small backwards game-clock step is a reset, not uint32 wrap.
    assert machine.update(21, decode_packet(packet(4, 3, 21))) is None
    assert machine.update(22, decode_packet(packet(10, 4, 30))) is None
    # Lap/race-time regression also discards the new partial lap.
    assert machine.update(23, decode_packet(packet(11, 2, 1))) is None
    assert machine.update(24, decode_packet(packet(12, 3, 10))) is None


def test_session_continuity_reports_duplicates_wraps_and_rejects_faults(
    tmp_path: Path,
) -> None:
    wrapped = StreamingSessionAdapter(
        make_session(
            tmp_path / "wrapped",
            "wrapped",
            telemetry_points=(
                (90, 0xFFFFFFF0, 1, 1.0),
                (100, 0xFFFFFFF0, 1, 1.0),
                (110, 5, 1, 2.0),
            ),
        ),
        benchmark(),
    ).validate()
    assert wrapped.accepted
    assert wrapped.telemetry_continuity == {
        "packets": 3,
        "duplicates": 1,
        "out_of_order": 0,
        "wraps": 1,
        "estimated_missing_packets": 0,
    }

    out_of_order = StreamingSessionAdapter(
        make_session(
            tmp_path / "out-of-order",
            "out-of-order",
            telemetry_points=((100, 100, 1, 1.0), (110, 90, 1, 2.0)),
        ),
        benchmark(),
    ).validate()
    assert not out_of_order.accepted
    assert out_of_order.telemetry_continuity["out_of_order"] == 1
    assert any("out-of-order" in reason for reason in out_of_order.reasons)

    missing = StreamingSessionAdapter(
        make_session(
            tmp_path / "missing",
            "missing",
            telemetry_points=((100, 10, 1, 1.0), (110, 100, 1, 2.0)),
        ),
        benchmark(),
    ).validate()
    assert not missing.accepted
    assert missing.telemetry_continuity["estimated_missing_packets"] == 5
    assert any("estimated missing" in reason for reason in missing.reasons)


def test_post_race_gaps_and_frames_are_excluded_from_primary_race(
    tmp_path: Path,
) -> None:
    adapter = StreamingSessionAdapter(
        make_session(
            tmp_path / "post-race",
            "post-race",
            frame_times=(100, 200, 300),
            controller_times=(100, 200, 300),
            telemetry_points=(
                (100, 10, 1, 1.0, True),
                (200, 100, 0, 0.0, False),
                (300, 200, 0, 0.0, False),
            ),
        ),
        benchmark(),
    )
    quality = adapter.validate()
    assert quality.accepted
    assert quality.aligned_frames == 1
    assert quality.alignment_rejections == 0
    assert quality.excluded_non_race_frames == 2
    assert quality.race_segments == ((100, 200),)
    assert quality.telemetry_continuity["estimated_missing_packets"] == 0
    assert "excluded non-race frames: 2" in quality.warnings
    assert [sample.frame_index for sample in adapter.aligned_frames()] == [0]


def test_substantial_race_segments_survive_pause_boundaries(
    tmp_path: Path,
) -> None:
    second = 1_000_000_000
    adapter = StreamingSessionAdapter(
        make_session(
            tmp_path / "segments",
            "segments",
            frame_times=(
                0,
                100 * second,
                200 * second,
                450 * second,
                500 * second,
                900 * second,
            ),
            controller_times=(
                0,
                100 * second,
                200 * second,
                450 * second,
                500 * second,
                900 * second,
            ),
            telemetry_points=(
                (0, 100, 1, 1.0, True),
                (5 * second, 105, 0, 0.0, False),
                (100 * second, 10, 1, 1.0, True),
                (200 * second, 20, 2, 10.0, True),
                (400 * second, 30, 0, 0.0, False),
                (500 * second, 5, 3, 1.0, True),
                (900 * second, 15, 4, 10.0, True),
            ),
        ),
        benchmark(),
    )
    quality = adapter.validate()
    assert quality.accepted
    assert quality.race_segments == (
        (100 * second, 400 * second),
        (500 * second, 900 * second + 33_334_001),
    )
    assert quality.aligned_frames == 4
    assert quality.excluded_non_race_frames == 2
    assert quality.telemetry_continuity["out_of_order"] == 0
    assert "excluded short race segments: 1" in quality.warnings
    assert [sample.frame_index for sample in adapter.aligned_frames()] == [1, 2, 4, 5]


def test_open_race_segment_does_not_absorb_stream_tails(tmp_path: Path) -> None:
    adapter = StreamingSessionAdapter(
        make_session(
            tmp_path / "open-tail",
            "open-tail",
            frame_times=(100, 100_000_000),
            controller_times=(100, 100_000_000),
        ),
        benchmark(),
    )
    quality = adapter.validate()
    assert quality.accepted
    assert quality.race_segments == ((100, 33_334_101),)
    assert quality.aligned_frames == 1
    assert quality.excluded_non_race_frames == 1
    assert [sample.frame_index for sample in adapter.aligned_frames()] == [0]


def test_candidate_lap_collection_is_bounded(tmp_path: Path) -> None:
    points = tuple(
        (index, index * 16, index, float(index + 1)) for index in range(1_028)
    )
    directory = make_session(
        tmp_path / "too-many-laps",
        "too-many-laps",
        telemetry_points=points,
    )
    with pytest.raises(DatasetError, match="too many candidate laps"):
        StreamingSessionAdapter(directory, benchmark()).validate()


def test_whole_session_split_is_deterministic_and_leakage_safe() -> None:
    ids = [f"session-{index}" for index in range(10)]
    first = assign_session_splits(ids)
    second = assign_session_splits(list(reversed(ids)))
    assert first == second
    assert tuple(map(len, (first.train, first.validation, first.test))) == (8, 1, 1)
    assert set(first.train).isdisjoint(first.validation)
    assert set(first.train).isdisjoint(first.test)
    assert set(first.validation).isdisjoint(first.test)
    assert assign_session_splits(ids[:3]).to_mapping().keys() == {
        "train",
        "validation",
        "test",
    }


def test_streaming_validation_health_alignment_and_warning(tmp_path: Path) -> None:
    valid = StreamingSessionAdapter(
        make_session(tmp_path / "valid", "valid", health="drop"), benchmark()
    ).validate()
    assert valid.accepted
    assert valid.warnings == ("capture drops reported: 1", "game build is unverified")
    assert valid.aligned_frames == 1
    assert valid.controls["steering_min"] == pytest.approx(0.25)
    assert valid.controls["steering_max"] == pytest.approx(0.25)
    assert valid.controls["steering_rms"] == pytest.approx(0.25)
    assert valid.controls["steering_nonzero_fraction"] == pytest.approx(1.0)
    ranged = StreamingSessionAdapter(
        make_session(tmp_path / "ranged", "ranged", frame_times=(50, 150)),
        benchmark(),
    ).validate()
    assert ranged.duration_ns == 100
    alignment = ranged.to_mapping()["alignment"]
    assert alignment == {
        "controller": {
            "count": 2,
            "sample_size": 2,
            "percentiles_exact": True,
            "p50_abs_delta_ns": 50.0,
            "p95_abs_delta_ns": 50.0,
            "p99_abs_delta_ns": 50.0,
            "max_abs_delta_ns": 50,
        },
        "telemetry": {
            "count": 2,
            "sample_size": 2,
            "percentiles_exact": True,
            "p50_abs_delta_ns": 50.0,
            "p95_abs_delta_ns": 50.0,
            "p99_abs_delta_ns": 50.0,
            "max_abs_delta_ns": 50,
        },
    }
    staggered = StreamingSessionAdapter(
        make_session(
            tmp_path / "staggered",
            "staggered",
            frame_times=(1_000_000_000, 2_000_000_000, 3_000_000_000),
            controller_times=(990_000_000, 2_000_000_000, 3_010_000_000),
            telemetry_points=(
                (1_005_000_000, 10, 1, 1.0),
                (2_005_000_000, 26, 1, 2.0),
                (3_005_000_000, 42, 1, 3.0),
            ),
        ),
        benchmark(),
    ).validate()
    staggered_mapping = staggered.to_mapping()
    assert staggered_mapping["duration_ns"] == 2_020_000_000
    assert staggered_mapping["stream_clocks"] == {
        "frames": {
            "first_session_ns": 1_000_000_000,
            "last_session_ns": 3_000_000_000,
            "span_ns": 2_000_000_000,
        },
        "controller": {
            "first_session_ns": 990_000_000,
            "last_session_ns": 3_010_000_000,
            "span_ns": 2_020_000_000,
        },
        "telemetry": {
            "first_session_ns": 1_005_000_000,
            "last_session_ns": 3_005_000_000,
            "span_ns": 2_000_000_000,
        },
    }
    assert staggered_mapping["rates"] == pytest.approx(
        {
            "frames_hz": 1.0,
            "controller_hz": 2 / 2.02,
            "telemetry_hz": 1.0,
        }
    )
    laps = StreamingSessionAdapter(
        make_session(
            tmp_path / "laps",
            "laps",
            telemetry_points=(
                (10, 10, 1, 1.0),
                (20, 20, 2, 10.0),
                (30, 30, 3, 20.0),
            ),
        ),
        benchmark(),
    ).validate()
    lap_mapping = laps.to_mapping()
    assert lap_mapping["candidate_lap_summary"] == {
        "count": 1,
        "median_duration_s": 10.0,
    }
    assert quality_document([laps])["summary"] == {
        "sessions": 1,
        "accepted_sessions": 1,
        "rejected_sessions": 0,
        "aligned_frames": 1,
        "alignment_rejections": 0,
        "excluded_non_race_frames": 0,
        "candidate_complete_laps": 1,
        "candidate_lap_median_duration_s": 10.0,
        "split_session_counts": {},
        "shard_counts": {},
    }
    fatal = StreamingSessionAdapter(
        make_session(tmp_path / "fatal", "fatal", health="stale"), benchmark()
    ).validate()
    assert not fatal.accepted and "health.stale=1" in fatal.reasons
    misaligned = StreamingSessionAdapter(
        make_session(
            tmp_path / "bad-align",
            "bad-align",
            frame_times=(100, 99_000_000),
            telemetry_points=((100, 10, 1, 1.0), (99_000_000, 26, 1, 2.0)),
        ),
        benchmark(),
    ).validate()
    assert not misaligned.accepted
    assert "more than 1% of race frames are misaligned" in misaligned.reasons


def test_rejects_non_string_payload_and_unsafe_session_ids(tmp_path: Path) -> None:
    directory = make_session(tmp_path / "payload", "payload")
    sidecar = directory / "telemetry.jsonl"
    document = json.loads(sidecar.read_text())
    document["payload_hex"] = 123
    sidecar.write_text(json.dumps(document) + "\n")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["telemetry.jsonl"] = hashlib.sha256(
        sidecar.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(DatasetError, match="must be a string"):
        StreamingSessionAdapter(directory, benchmark()).validate()

    for index, session_id in enumerate(("../escape", "a" * 65)):
        unsafe = make_session(tmp_path / f"unsafe-{index}", session_id)
        with pytest.raises(DatasetError, match="session ID"):
            StreamingSessionAdapter(unsafe, benchmark())
    with pytest.raises(DatasetError, match="at least one session"):
        build_dataset(tmp_path / "empty", [], benchmark())


def test_binary_sidecar_equality_is_strict(tmp_path: Path) -> None:
    directory = make_session(tmp_path / "session", "strict")
    sidecar = directory / "telemetry.jsonl"
    document = json.loads(sidecar.read_text())
    document["payload_hex"] = packet(11).hex()
    sidecar.write_text(json.dumps(document) + "\n")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["telemetry.jsonl"] = hashlib.sha256(
        sidecar.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(DatasetError, match="differ"):
        StreamingSessionAdapter(directory, benchmark()).validate()


def test_build_is_deterministic_bounded_and_refuses_overwrite(tmp_path: Path) -> None:
    sessions = [
        make_session(tmp_path / f"session-{index}", f"session-{index}")
        for index in range(3)
    ]
    first = build_dataset(
        tmp_path / "dataset-a", sessions, benchmark(), max_samples_per_shard=1
    )
    second = build_dataset(
        tmp_path / "dataset-b",
        list(reversed(sessions)),
        benchmark(),
        max_samples_per_shard=1,
    )
    assert first == second
    assert first["schema"] == 1 and first["temporal_windows"] is False
    assert first["splits"] == second["splits"]
    for session in first["sessions"]:
        source = tmp_path / session["session_id"] / "manifest.json"
        assert (
            session["source_manifest_sha256"]
            == hashlib.sha256(source.read_bytes()).hexdigest()
        )
    forbidden_telemetry = {
        "horizon_extra",
        "optional_trailing_byte",
        "accel",
        "brake",
        "clutch",
        "handbrake",
        "steer",
        "normalized_driving_line",
        "normalized_ai_brake_difference",
    }
    for shard in first["shards"]:
        assert shard["samples"] <= 1
        with tarfile.open(tmp_path / "dataset-a" / shard["path"], "r:") as archive:
            names = archive.getnames()
            assert (
                len(names) == 2
                and names[0].endswith(".jpg")
                and names[1].endswith(".json")
            )
            extracted = archive.extractfile(names[1])
            assert extracted is not None
            metadata = json.loads(extracted.read())
            assert set(metadata["telemetry"]).isdisjoint(forbidden_telemetry)
            required_telemetry = {
                "acceleration_x",
                "velocity_x",
                "angular_velocity_x",
                "yaw",
                "position_x",
                "wheel_rotation_speed_front_left",
                "tire_slip_ratio_front_left",
                "tire_slip_angle_front_left",
                "tire_combined_slip_front_left",
                "normalized_suspension_travel_front_left",
                "suspension_travel_meters_front_left",
                "power",
                "tire_temp_front_left",
                "gear",
                "race_position",
                "current_race_time",
            }
            assert required_telemetry <= set(metadata["telemetry"])
    assert (tmp_path / "dataset-a" / "quality.json").is_file()
    assert (tmp_path / "dataset-a" / "quality.md").is_file()
    with pytest.raises(DatasetError, match="already exists"):
        build_dataset(tmp_path / "dataset-a", sessions, benchmark())


def test_dataset_cli_refuses_report_overwrite_and_bounds_shards(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = make_session(tmp_path / "cli-session", "cli-session")
    config = "configs/benchmark/horizon_festival_circuit.toml"
    reports = tmp_path / "reports"
    assert (
        main(
            [
                "dataset-validate",
                str(session),
                "--config",
                config,
                "--report-dir",
                str(reports),
            ]
        )
        == 0
    )
    capsys.readouterr()
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "dataset-validate",
                str(session),
                "--config",
                config,
                "--report-dir",
                str(reports),
            ]
        )
    capsys.readouterr()
    output = tmp_path / "cli-dataset"
    assert (
        main(
            [
                "dataset-build",
                str(output),
                str(session),
                "--config",
                config,
                "--max-samples-per-shard",
                "1",
            ]
        )
        == 0
    )
    manifest = json.loads((output / "dataset-manifest.json").read_text())
    assert manifest["max_samples_per_shard"] == 1
