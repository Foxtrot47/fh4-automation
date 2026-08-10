from __future__ import annotations

import json
from pathlib import Path

import pytest

from fh4_agent.capture import (
    CameraProfile,
    FrameChunkWriter,
    SessionRecorder,
    read_frame_chunks,
    validate_manifest,
)
from fh4_agent.contracts import BenchmarkIdentity
from fh4_agent.sync import (
    BoundedQueue,
    QueueOverloadError,
    SessionClock,
    align_nearest,
)


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


def test_clock_alignment_and_lossless_queue() -> None:
    clock = SessionClock(lambda: 1_000)
    assert clock.stamp(1_025).session_ns == 25
    samples = [type("S", (), {"session_ns": 10})(), type("S", (), {"session_ns": 20})()]
    assert align_nearest(15, samples).index == 0
    queue = BoundedQueue[int](1, policy="reject")
    queue.put(1)
    with pytest.raises(QueueOverloadError):
        queue.put(2)
    assert queue.health.overload_failures == 1


def test_frame_tail_recovery_and_manifest_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "frames.fh4jpg"
    source_ns = 1 << 50
    with FrameChunkWriter(path) as writer:
        writer.append(b"jpeg", 1, source_ns)
    path.write_bytes(path.read_bytes() + b"partial")
    chunks = list(read_frame_chunks(path, recover_tail=True))
    assert len(chunks) == 1
    assert chunks[0].source_clock_ns == source_ns

    directory = tmp_path / "session"
    recorder = SessionRecorder(
        directory, session_id="s1", benchmark=benchmark(), profile=CameraProfile()
    )

    def reject_unbounded_read(self: Path) -> bytes:
        raise AssertionError(f"unbounded read_bytes used for {self}")

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)
    recorder.finalize()
    manifest = validate_manifest(
        directory / "manifest.json", benchmark(), CameraProfile()
    )
    assert manifest.complete
    document = json.loads((directory / "manifest.json").read_text())
    assert document["profile_digest"]
