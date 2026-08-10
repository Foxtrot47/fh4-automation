"""Explicit bounded session lifecycle for read-only recording."""
# ruff: noqa: E501

from __future__ import annotations

import threading
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..contracts import BenchmarkIdentity
from .recording import SessionManifest, SessionRecorder


class SessionState(StrEnum):
    CREATED = "created"
    RECORDING = "recording"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class SessionLifecycle:
    def __init__(
        self,
        directory: str | Path,
        *,
        session_id: str,
        benchmark: BenchmarkIdentity,
        profile: object,
        max_frames: int,
        max_seconds: float,
        monotonic_ns: Any = time.monotonic_ns,
    ) -> None:
        if (
            isinstance(max_frames, bool)
            or not isinstance(max_frames, int)
            or max_frames <= 0
        ):
            raise ValueError("max_frames must be positive")
        if (
            isinstance(max_seconds, bool)
            or not isinstance(max_seconds, (int, float))
            or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be positive")
        self.max_frames = max_frames
        self.max_seconds = float(max_seconds)
        self.state = SessionState.CREATED
        self._monotonic_ns = monotonic_ns
        self._origin_ns = monotonic_ns()
        self._lock = threading.RLock()
        self.recorder = SessionRecorder(
            directory, session_id=session_id, benchmark=benchmark, profile=profile
        )
        self._frames = 0
        self._deadline_ns = self._origin_ns + int(self.max_seconds * 1_000_000_000)

    def start(self) -> None:
        with self._lock:
            if self.state is not SessionState.CREATED:
                raise RuntimeError("session cannot be started from its current state")
            self._origin_ns = self._monotonic_ns()
            self._deadline_ns = self._origin_ns + int(self.max_seconds * 1_000_000_000)
            self.state = SessionState.RECORDING

    def append_frame(self, frame: Any) -> None:
        with self._lock:
            if self.state is not SessionState.RECORDING:
                raise RuntimeError("session is not recording")
            if self._frames >= self.max_frames:
                raise RuntimeError("session max_frames reached")
            stamp = getattr(getattr(frame, "timestamp", None), "monotonic_ns", None)
            if (
                isinstance(stamp, int) and stamp >= self._deadline_ns
            ) or self._monotonic_ns() >= self._deadline_ns:
                raise RuntimeError("session max_seconds reached")
            self.recorder.append_frame(frame)
            self._frames += 1

    def finalize(self) -> SessionManifest:
        if self.state is not SessionState.RECORDING:
            raise RuntimeError("session is not recording")
        result = self.recorder.finalize()
        self.state = SessionState.FINALIZED
        return result

    def abort(self) -> None:
        if self.state is SessionState.CREATED:
            self.recorder.close()
        elif self.state is SessionState.RECORDING:
            self.recorder.close()
        self.state = SessionState.ABORTED

    def close(self) -> None:
        if self.state not in {SessionState.FINALIZED, SessionState.ABORTED}:
            self.abort()


__all__ = ["SessionLifecycle", "SessionState"]
