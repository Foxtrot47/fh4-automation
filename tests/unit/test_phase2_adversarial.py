from __future__ import annotations

import ctypes
import json
import math
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from fh4_agent.capture import (
    CameraFrame,
    CameraProfile,
    CaptureCoordinator,
    FrameChunkWriter,
    RecordingError,
    SessionLifecycle,
    SessionRecorder,
    TelemetryInput,
    encode_jpeg,
    read_frame_chunks,
    recover_session,
    replay_session,
    validate_manifest,
)
from fh4_agent.capture import camera as camera_module
from fh4_agent.capture import telemetry as telemetry_module
from fh4_agent.capture.health import latency_summary, percentile
from fh4_agent.contracts import (
    BenchmarkIdentity,
    ControllerSample,
    RequestedControlAction,
)
from fh4_agent.input import (
    XINPUT_GAMEPAD_A,
    ControllerDisconnected,
    XInputController,
)
from fh4_agent.sync import BoundedQueue, QueueOverloadError, SessionClock, TimelineStamp


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
    image = Image.new("RGB", (960, 540), (20, 40, 60))
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()


def frame(session_ns: int = 0) -> CameraFrame:
    stamp = TimelineStamp(session_ns, session_ns, source_clock_ns=10_000 + session_ns)
    return CameraFrame(0, stamp, jpeg())


class EmptySource:
    def __init__(self) -> None:
        self.closed = False

    def frames(self, *, max_frames: int | None = None):
        yield from ()

    def samples(self, *, max_samples: int | None = None):
        yield from ()

    def records(self, *, max_packets: int | None = None, timeout_s: float = 0.25):
        yield from ()

    def close(self) -> None:
        self.closed = True


class DisconnectingController(EmptySource):
    def samples(self, *, max_samples: int | None = None):
        raise ControllerDisconnected("test disconnect")
        yield  # pragma: no cover


class DelayedTelemetry(EmptySource):
    def records(self, *, max_packets: int | None = None, timeout_s: float = 0.25):
        time.sleep(0.02)
        yield TelemetryInput(b"x" * 323, 1, ("fake", 5300), 1, 1)


class CloseFailCamera(EmptySource):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("camera close failed")


class FiniteCamera:
    def __init__(self, values: list[CameraFrame]) -> None:
        self.values = values
        self.closed = False

    def frames(self, *, max_frames: int | None = None):
        yield from self.values

    def close(self) -> None:
        self.closed = True


class FakeXInputFunction:
    def __init__(self, states: list[tuple[int, dict[str, int]]]) -> None:
        self.states = states
        self.calls: list[int] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, slot: int, pointer: object) -> int:
        self.calls.append(slot)
        status, values = self.states.pop(0)
        if status:
            return status

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

        state = ctypes.cast(pointer, ctypes.POINTER(State)).contents
        state.packet = values["packet"]
        for name in (
            "buttons",
            "left_trigger",
            "right_trigger",
            "thumb_lx",
            "thumb_ly",
            "thumb_rx",
            "thumb_ry",
        ):
            setattr(state.gamepad, name, values[name])
        return 0


class FakeDll:
    def __init__(self, function: FakeXInputFunction) -> None:
        self.XInputGetState = function


def test_disconnect_preserves_other_streams_and_cleanup_is_total(
    tmp_path: Path,
) -> None:
    recorder = SessionRecorder(
        tmp_path / "disconnect",
        session_id="disconnect",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    camera = EmptySource()
    telemetry = DelayedTelemetry()
    manifest = CaptureCoordinator(
        recorder,
        camera,
        DisconnectingController(),
        telemetry,
        max_seconds=1,
        max_frames=1,
    ).run()
    assert manifest.health["disconnects"] == 1
    assert manifest.health["stale"] == 1
    assert recover_session(tmp_path / "disconnect")["telemetry"] == 1
    assert camera.closed and telemetry.closed

    failed_recorder = SessionRecorder(
        tmp_path / "close-fault",
        session_id="close-fault",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    failing_camera = CloseFailCamera()
    controller, other_telemetry = EmptySource(), EmptySource()
    coordinator = CaptureCoordinator(
        failed_recorder,
        failing_camera,
        controller,
        other_telemetry,
        max_seconds=1,
        max_frames=1,
    )
    with pytest.raises(RuntimeError, match="coordinator fault"):
        coordinator.run()
    assert failing_camera.close_calls >= 1
    assert controller.closed and other_telemetry.closed
    with pytest.raises(RecordingError, match="closed"):
        failed_recorder.append_controller(object())


def test_bounds_and_lifecycle_are_adversarial_and_deterministic(tmp_path: Path) -> None:
    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="s",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    with pytest.raises(ValueError):
        CaptureCoordinator(
            recorder,
            EmptySource(),
            EmptySource(),
            EmptySource(),
            max_seconds=0,
            max_frames=1,
        )
    with pytest.raises(ValueError):
        CaptureCoordinator(
            recorder,
            EmptySource(),
            EmptySource(),
            EmptySource(),
            max_seconds=1,
            max_frames=0,
        )
    lifecycle = SessionLifecycle(
        tmp_path / "bounded",
        session_id="bounded",
        benchmark=benchmark(),
        profile=CameraProfile(),
        max_frames=1,
        max_seconds=100,
        monotonic_ns=lambda: 0,
    )
    lifecycle.start()
    lifecycle.append_frame(frame())
    with pytest.raises(RuntimeError, match="max_frames"):
        lifecycle.append_frame(frame())
    lifecycle.finalize()

    now = [0]
    timed = SessionLifecycle(
        tmp_path / "timed",
        session_id="timed",
        benchmark=benchmark(),
        profile=CameraProfile(),
        max_frames=2,
        max_seconds=1e-9,
        monotonic_ns=lambda: now[0],
    )
    timed.start()
    now[0] = 1
    with pytest.raises(RuntimeError, match="max_seconds"):
        timed.append_frame(frame())
    timed.abort()
    recorder.close()

    camera = FiniteCamera([frame(0), frame(1), frame(2)])
    controller, telemetry = EmptySource(), EmptySource()
    capture = SessionRecorder(
        tmp_path / "capture",
        session_id="c",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    coordinator = CaptureCoordinator(
        capture,
        camera,
        controller,
        telemetry,
        max_seconds=10,
        max_frames=2,
    )
    manifest = coordinator.run()
    assert manifest.complete
    assert recover_session(tmp_path / "capture")["frames"] == 2
    assert camera.closed and controller.closed and telemetry.closed


def test_stop_join_and_producer_after_finalize_are_safe(tmp_path: Path) -> None:
    sources = [EmptySource(), EmptySource(), EmptySource()]
    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="s",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    coordinator = CaptureCoordinator(recorder, *sources, max_seconds=10, max_frames=1)
    coordinator.start()
    coordinator.stop()
    coordinator.join(timeout_s=1)
    assert coordinator.stopped
    assert all(source.closed for source in sources)
    recorder.finalize()
    with pytest.raises(RecordingError):
        recorder.append_frame(frame())
    with pytest.raises(RecordingError):
        recorder.append_controller(
            ControllerSample(0, RequestedControlAction(0, 0, 0), session_ns=0)
        )
    with pytest.raises(RecordingError):
        recorder.append_telemetry(b"x" * 323, 0, ("fake", 1))


def test_camera_and_controller_drop_policies_are_explicit(tmp_path: Path) -> None:
    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="s",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    coordinator = CaptureCoordinator(
        recorder,
        EmptySource(),
        EmptySource(),
        EmptySource(),
        max_seconds=1,
        max_frames=1,
        camera_capacity=1,
        controller_capacity=1,
    )
    coordinator._put_drop_oldest(coordinator._camera_q, "camera-old")
    coordinator._put_drop_oldest(coordinator._camera_q, "camera-new")
    coordinator._put_drop_oldest(coordinator._controller_q, "controller-old")
    coordinator._put_drop_oldest(coordinator._controller_q, "controller-new")
    assert coordinator._camera_q.get_nowait() == "camera-new"
    assert coordinator._controller_q.get_nowait() == "controller-new"
    assert recorder.health.dropped == 2
    recorder.close()

    queue = BoundedQueue[int](1, policy="drop_newest")
    assert queue.put(1)
    assert not queue.put(2)
    assert queue.get() == 1
    assert queue.health.dropped == 1
    rejecting = BoundedQueue[int](1)
    rejecting.put(1)
    with pytest.raises(QueueOverloadError):
        rejecting.put(2)


def test_telemetry_overload_fault_preserves_exact_datagram_bytes(
    tmp_path: Path,
) -> None:
    payloads = [bytes([index]) * 323 for index in (1, 2)]

    class Telemetry:
        def __init__(self) -> None:
            self.closed = False

        def records(self, *, max_packets: int | None = None, timeout_s: float = 0.25):
            for index, payload in enumerate(payloads):
                yield TelemetryInput(payload, index, ("fake", 5300))

        def close(self) -> None:
            self.closed = True

    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="s",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    telemetry = Telemetry()
    coordinator = CaptureCoordinator(
        recorder,
        EmptySource(),
        EmptySource(),
        telemetry,
        max_seconds=10,
        max_frames=1,
        telemetry_capacity=1,
    )
    with pytest.raises(RuntimeError, match="capture coordinator fault"):
        coordinator.run()
    assert telemetry.closed
    assert recorder.health.writer_faults == 1
    # The producer fault is reported before finalization; the durable stream is
    # still readable and any accepted datagram remains byte-for-byte exact.
    assert (
        list(
            telemetry_module.CaptureReader(
                tmp_path / "session" / "telemetry.fh4cap", strict_packet_lengths=False
            )
        )[0].payload
        == payloads[0]
    )


def test_xinput_raw_packet_buttons_disconnect_and_deterministic_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "packet": 17,
        "buttons": 0x1000,
        "left_trigger": 12,
        "right_trigger": 240,
        "thumb_lx": 20_000,
        "thumb_ly": -2_000,
        "thumb_rx": 3,
        "thumb_ry": 4,
    }
    function = FakeXInputFunction([(0, values), (1167, values)])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "fh4_agent.input.xinput.ctypes.WinDLL", lambda name: FakeDll(function)
    )
    clock = SessionClock(lambda: 0)
    controller = XInputController(clock, monotonic_ns=lambda: 0)
    state, stamp = controller.read_state()
    assert function.calls == [0]
    assert state.packet == 17 and state.buttons == 0x1000
    assert state.to_action().handbrake == 1.0
    assert state.to_action().throttle == pytest.approx(240 / 255)
    assert stamp.session_ns == 0 and stamp.source_clock_ns == 0
    with pytest.raises(ControllerDisconnected):
        controller.read_state()
    assert controller.disconnect_count == 1

    class PacingClock:
        now_ns = 0
        sleeps: list[float] = []

        def now(self) -> int:
            return self.now_ns

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now_ns += int(seconds * 1_000_000_000)

    pacing = PacingClock()
    function = FakeXInputFunction([(0, values), (0, {**values, "packet": 18})])
    monkeypatch.setattr(
        "fh4_agent.input.xinput.ctypes.WinDLL", lambda name: FakeDll(function)
    )
    monkeypatch.setattr("fh4_agent.input.xinput.time.sleep", pacing.sleep)
    sampled = list(
        XInputController(
            SessionClock(lambda: 0), monotonic_ns=pacing.now, poll_hz=2
        ).samples(max_samples=2)
    )
    assert function.calls == [0, 0]
    assert [sample.packet for sample in sampled] == [17, 18]
    assert pacing.sleeps == [pytest.approx(0.5)]
    assert [sample.source_clock_ns for sample in sampled] == [0, 500_000_000]


def test_dxcam_lifecycle_copy_timestamp_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = np.zeros((1440, 2560, 3), dtype=np.uint8)

    class FakeCamera:
        def __init__(self) -> None:
            self.started = None
            self.stopped = False
            self.released = False
            self.calls = 0

        def start(self, **kwargs: object) -> None:
            self.started = kwargs

        def get_latest_frame(self, *, with_timestamp: bool) -> tuple[np.ndarray, float]:
            assert with_timestamp
            self.calls += 1
            return source, 0.000003

        def stop(self) -> None:
            self.stopped = True

        def release(self) -> None:
            self.released = True

    fake_camera = FakeCamera()
    create_calls: list[dict[str, object]] = []

    def create(**kwargs: object) -> FakeCamera:
        create_calls.append(kwargs)
        return fake_camera

    fake_module = SimpleNamespace(create=create)
    monkeypatch.setitem(sys.modules, "dxcam", fake_module)
    ticks = iter((2_000,))
    camera = camera_module.DXcamCamera(
        SessionClock(lambda: 1_000), monotonic_ns=lambda: next(ticks)
    )
    captured = list(camera.frames(max_frames=1))
    assert create_calls == [
        {
            "output_idx": 0,
            "output_color": "BGR",
            "backend": "dxgi",
            "processor_backend": "numpy",
            "max_buffer_len": 8,
        }
    ]
    assert fake_camera.started == {
        "region": (0, 0, 2560, 1440),
        "target_fps": 30,
        "video_mode": True,
    }
    assert fake_camera.calls == 1
    assert captured[0].session_ns == 1_000
    assert captured[0].source_clock_ns == 3_000
    assert Image.open(__import__("io").BytesIO(captured[0].jpeg)).size == (960, 540)
    camera.close()
    camera.close()
    assert fake_camera.stopped and fake_camera.released


def test_jpeg_validity_dimensions_and_length_bounds(tmp_path: Path) -> None:
    encoded = encode_jpeg(np.zeros((1440, 2560, 3), dtype=np.uint8))
    assert encoded[:2] == b"\xff\xd8"
    assert Image.open(__import__("io").BytesIO(encoded)).size == (960, 540)
    with pytest.raises(ValueError):
        camera_module._validate_jpeg(b"not-jpeg", CameraProfile())
    with pytest.raises(TypeError):
        encode_jpeg(encoded)

    frame_path = tmp_path / "frames.fh4jpg"
    with FrameChunkWriter(frame_path) as writer:
        writer.append(b"J", 0)
    data = bytearray(frame_path.read_bytes())
    _, _, metadata_len = struct.unpack_from("<8sHI", data, 0)
    record_offset = 14 + metadata_len + 4
    struct.pack_into("<I", data, record_offset + 4, 4 * 1024 * 1024 + 1)
    frame_path.write_bytes(data)
    with pytest.raises(RecordingError):
        list(read_frame_chunks(frame_path))


def test_integer_clock_separation_and_malformed_capture_lengths(tmp_path: Path) -> None:
    clock = SessionClock(lambda: 10_000)
    stamp = clock.stamp(12_345, source_clock_ns=99_000, game_clock_ms=123)
    assert (
        stamp.session_ns,
        stamp.monotonic_ns,
        stamp.source_clock_ns,
        stamp.game_clock_ms,
    ) == (2_345, 12_345, 99_000, 123)
    assert all(
        isinstance(value, int)
        for value in (
            stamp.session_ns,
            stamp.monotonic_ns,
            stamp.source_clock_ns,
            stamp.game_clock_ms,
        )
    )

    path = tmp_path / "capture.fh4cap"
    with telemetry_module.CaptureWriter(path) as writer:
        writer.append_datagram(b"x" * 323, 1.0, ("fake", 1))
    data = bytearray(path.read_bytes())
    header_size, metadata_len = telemetry_module.CaptureWriter._validate_header(
        __import__("io").BytesIO(data)
    )
    struct.pack_into("<H", data, header_size + 4, 0xFFFF)
    path.write_bytes(data)
    with pytest.raises(telemetry_module.CaptureError):
        list(telemetry_module.CaptureReader(path))

    exact_path = tmp_path / "exact-ns.fh4cap"
    exact_ns = 9_007_199_254_740_993
    with telemetry_module.CaptureWriter(exact_path) as writer:
        writer.append_datagram_ns(b"x" * 323, exact_ns, ("fake", 1))
    exact_record = list(telemetry_module.CaptureReader(exact_path))[0]
    assert exact_record.timestamp_monotonic_ns == exact_ns
    assert exact_record.session_ns == exact_ns


def test_non_finite_health_and_all_stream_tail_recovery_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError):
        percentile([1.0, math.nan], 50)
    with pytest.raises(ValueError):
        latency_summary([math.inf])

    directory = tmp_path / "session"
    recorder = SessionRecorder(
        directory, session_id="s", benchmark=benchmark(), profile=CameraProfile()
    )
    recorder.append_frame(frame())
    recorder.append_controller(
        ControllerSample(1, RequestedControlAction(0.1, 0.2, 0.3), session_ns=1)
    )
    payload = b"z" * 323
    recorder.append_telemetry(payload, 2, ("fake", 2))
    recorder.record_drop()
    recorder.finalize()
    for name, tail in (
        ("frames.fh4jpg", b"partial"),
        ("controller.jsonl", b"{bad"),
        ("telemetry.jsonl", b"{bad"),
        ("health.jsonl", b"{bad"),
        ("telemetry.fh4cap", b"partial"),
    ):
        with (directory / name).open("ab") as file:
            file.write(tail)
    recovered = recover_session(directory)
    assert recovered == {"frames": 1, "controller": 1, "telemetry": 1, "health": 1}

    def reject_unbounded_read(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError(f"unbounded read_text used for {self}")

    monkeypatch.setattr(Path, "read_text", reject_unbounded_read)
    events = list(replay_session(directory))
    assert [event["stream"] for event in events] == [
        "frame",
        "health",
        "controller",
        "telemetry",
    ]
    assert events[0]["jpeg"] == jpeg()
    assert events[3]["payload_hex"] == payload.hex()


def test_manifest_digest_file_and_metadata_validation(tmp_path: Path) -> None:
    directory = tmp_path / "session"
    recorder = SessionRecorder(
        directory, session_id="s", benchmark=benchmark(), profile=CameraProfile()
    )
    manifest = recorder.finalize()
    path = directory / "manifest.json"
    assert validate_manifest(path, benchmark(), CameraProfile()).files == manifest.files

    document = json.loads(path.read_text())
    document["profile_digest"] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(RecordingError, match="profile digest"):
        validate_manifest(path, benchmark(), CameraProfile())

    path.write_text(json.dumps(manifest.to_mapping()))
    frame_path = directory / "frames.fh4jpg"
    original_frames = frame_path.read_bytes()
    frame_path.write_bytes(original_frames + b"tamper")
    with pytest.raises(RecordingError, match="checksum"):
        validate_manifest(path, benchmark(), CameraProfile())

    path.write_text(json.dumps(manifest.to_mapping()))
    frame_path.write_bytes(original_frames)
    document = json.loads(path.read_text())
    document["metadata"]["session_id"] = "other"
    path.write_text(json.dumps(document))
    with pytest.raises(RecordingError, match="metadata"):
        validate_manifest(path, benchmark(), CameraProfile())

    document = manifest.to_mapping()
    document["files"] = {}
    path.write_text(json.dumps(document))
    with pytest.raises(RecordingError, match="required stream"):
        validate_manifest(path, benchmark(), CameraProfile())

    document = manifest.to_mapping()
    document["health"] = {key: "0" for key in document["health"]}
    path.write_text(json.dumps(document))
    with pytest.raises(RecordingError, match="manifest"):
        validate_manifest(path, benchmark(), CameraProfile())


def test_recorder_never_overwrites_an_existing_session_directory(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "frames.fh4jpg"
    sentinel.write_bytes(b"keep")
    with pytest.raises(RecordingError, match="already exists"):
        SessionRecorder(
            existing,
            session_id="s",
            benchmark=benchmark(),
            profile=CameraProfile(),
        )
    assert sentinel.read_bytes() == b"keep"
    assert sorted(path.name for path in existing.iterdir()) == ["frames.fh4jpg"]


def test_recorder_rejects_invalid_telemetry_before_finalizing(tmp_path: Path) -> None:
    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="s",
        benchmark=benchmark(),
        profile=CameraProfile(),
    )
    invalid_controller = SimpleNamespace(session_ns=1.5, to_mapping=lambda: {})
    with pytest.raises(RecordingError, match="integer session_ns"):
        recorder.append_controller(invalid_controller)
    with pytest.raises(RecordingError, match="323 or 324"):
        recorder.append_telemetry(b"x", 0, ("fake", 1))
    manifest = recorder.finalize()
    assert manifest.complete


def test_session_capture_cli_uses_injected_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fh4_agent import cli

    monkeypatch.setattr(
        cli,
        "DXcamCamera",
        lambda clock, profile: FiniteCamera([frame(0)]),
    )
    monkeypatch.setattr(cli, "XInputController", lambda clock: EmptySource())
    monkeypatch.setattr(
        cli,
        "UdpTelemetryReceiver",
        lambda *args, **kwargs: EmptySource(),
    )
    monkeypatch.setattr(
        cli,
        "SessionTelemetrySource",
        lambda receiver, clock: EmptySource(),
    )
    output = tmp_path / "cli-session"
    assert (
        cli.main(
            [
                "session-capture",
                str(output),
                "--config",
                "configs/benchmark/horizon_festival_circuit.toml",
                "--game-build",
                "test-build",
                "--max-frames",
                "1",
                "--max-seconds",
                "1",
            ]
        )
        == 0
    )
    assert (output / "manifest.json").is_file()


def test_session_capture_cli_starts_after_physical_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fh4_agent import cli

    class GateController(EmptySource):
        def __init__(self) -> None:
            super().__init__()
            self.states = iter((0, XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_A, 0))
            self.reads = 0

        def read_state(self) -> tuple[SimpleNamespace, None]:
            self.reads += 1
            return SimpleNamespace(buttons=next(self.states)), None

    gate = GateController()
    capture_controller = EmptySource()
    controllers = iter((gate, capture_controller))
    monkeypatch.setattr(cli, "XInputController", lambda clock: next(controllers))
    monkeypatch.setattr(
        cli,
        "DXcamCamera",
        lambda clock, profile: FiniteCamera([frame(0)]),
    )
    monkeypatch.setattr(
        cli, "UdpTelemetryReceiver", lambda *args, **kwargs: EmptySource()
    )
    monkeypatch.setattr(
        cli,
        "SessionTelemetrySource",
        lambda receiver, clock: EmptySource(),
    )
    output = tmp_path / "gated-cli-session"
    assert (
        cli.main(
            [
                "session-capture",
                str(output),
                "--config",
                "configs/benchmark/horizon_festival_circuit.toml",
                "--game-build",
                "test-build",
                "--max-frames",
                "1",
                "--max-seconds",
                "1",
                "--start-on-a-release",
            ]
        )
        == 0
    )
    stderr = capsys.readouterr().err
    assert "Waiting for physical XInput A hold" in stderr
    assert "Armed; release A" in stderr
    assert "A released; capture starting" in stderr
    assert gate.reads == 4
    assert gate.closed and capture_controller.closed
    assert (output / "manifest.json").is_file()


def test_capture_progress_reports_elapsed_and_remaining(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fh4_agent import cli

    times = iter((100.0, 102.0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(times))

    class OneTickStop:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, _interval_s: float) -> bool:
            self.stopped = True
            return True

    cli._capture_progress(5.0, OneTickStop())  # type: ignore[arg-type]
    assert "[00:02 elapsed | 00:03 remaining] Capturing" in capsys.readouterr().err


def test_session_capture_cli_cleans_every_resource_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fh4_agent import cli

    camera, controller, receiver, telemetry = (
        EmptySource(),
        EmptySource(),
        EmptySource(),
        EmptySource(),
    )
    monkeypatch.setattr(cli, "DXcamCamera", lambda clock, profile: camera)
    monkeypatch.setattr(cli, "XInputController", lambda clock: controller)
    monkeypatch.setattr(
        cli,
        "UdpTelemetryReceiver",
        lambda *args, **kwargs: receiver,
    )
    monkeypatch.setattr(
        cli,
        "SessionTelemetrySource",
        lambda source, clock: telemetry,
    )

    def fail_run(self: object) -> object:
        raise RuntimeError("injected capture failure")

    monkeypatch.setattr(cli.CaptureCoordinator, "run", fail_run)
    output = tmp_path / "failed-cli-session"
    with pytest.raises(RuntimeError, match="injected capture failure"):
        cli.main(
            [
                "session-capture",
                str(output),
                "--config",
                "configs/benchmark/horizon_festival_circuit.toml",
                "--game-build",
                "test-build",
                "--max-frames",
                "1",
                "--max-seconds",
                "1",
                "--progress",
            ]
        )
    assert camera.closed and controller.closed and receiver.closed and telemetry.closed
    assert all(
        thread.name != "fh4-capture-progress" for thread in threading.enumerate()
    )
    assert recover_session(output) == {
        "frames": 0,
        "controller": 0,
        "telemetry": 0,
        "health": 0,
    }


def test_importing_hardware_adapters_never_opens_devices() -> None:
    code = """
import builtins, ctypes, socket
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'dxcam', 'win32api'}:
        raise AssertionError('hardware module imported eagerly')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def fail_dll(*args, **kwargs):
    raise AssertionError('DLL opened eagerly')
def fail_socket(*args, **kwargs):
    raise AssertionError('socket opened eagerly')
ctypes.WinDLL = fail_dll
socket.socket = fail_socket
import fh4_agent.capture.camera
import fh4_agent.input.xinput
import fh4_agent.capture.telemetry
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        env={"PYTHONPATH": str(Path(__file__).parents[2] / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
