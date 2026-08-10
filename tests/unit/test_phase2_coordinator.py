from __future__ import annotations

from pathlib import Path

from fh4_agent.capture import (
    CameraProfile,
    CaptureCoordinator,
    SessionRecorder,
    recover_session,
)
from fh4_agent.contracts import BenchmarkIdentity
from fh4_agent.input import XInputState


def _benchmark() -> BenchmarkIdentity:
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


class _EmptyCamera:
    def frames(self, *, max_frames: int | None = None):
        yield from ()

    def close(self) -> None:
        pass


class _EmptyController:
    def samples(self, *, max_samples: int | None = None):
        yield from ()

    def close(self) -> None:
        pass


class _FakeTelemetry:
    def records(self, *, max_packets: int | None = None, timeout_s: float = 0.25):
        yield type(
            "Datagram",
            (),
            {
                "payload": b"x" * 323,
                "timestamp_monotonic_s": 1.0,
                "source": ("fake", 5300),
            },
        )()

    def close(self) -> None:
        pass


def test_xinput_a_maps_handbrake_and_retains_raw_state() -> None:
    state = XInputState(buttons=0x1000, packet=7, left_trigger=10)
    assert state.to_action().handbrake == 1.0
    assert state.to_mapping()["packet"] == 7


def test_coordinator_finalizes_lossless_telemetry_with_bounds(tmp_path: Path) -> None:
    recorder = SessionRecorder(
        tmp_path / "session",
        session_id="fake",
        benchmark=_benchmark(),
        profile=CameraProfile(),
    )
    coordinator = CaptureCoordinator(
        recorder,
        _EmptyCamera(),
        _EmptyController(),
        _FakeTelemetry(),
        max_seconds=0.01,
        max_frames=1,
        monotonic_ns=lambda: 1_000_000_000,
    )
    manifest = coordinator.run()
    assert manifest.complete
    assert (tmp_path / "session" / "manifest.json").is_file()
    assert recover_session(tmp_path / "session")["telemetry"] == 1
