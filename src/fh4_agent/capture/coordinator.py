"""Threaded, bounded, device-independent session capture coordinator."""

# ruff: noqa: E501, E701, E702
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ..input import ControllerDisconnected
from ..sync import QueueOverloadError, SessionClock
from ..telemetry import decode_packet
from .recording import SessionManifest, SessionRecorder


@dataclass(frozen=True, slots=True)
class TelemetryInput:
    payload: bytes
    session_ns: int
    source: tuple[str, int]
    source_clock_ns: int | None = None
    game_clock_ms: int | None = None


class SessionTelemetrySource:
    """Stamp lazy UDP datagrams onto the session clock before queueing."""

    def __init__(self, receiver: Any, clock: SessionClock) -> None:
        self.receiver = receiver
        self.clock = clock

    def records(
        self, *, max_packets: int | None = None, timeout_s: float = 0.25
    ) -> Iterator[TelemetryInput]:
        for datagram in self.receiver.records(
            max_packets=max_packets, timeout_s=timeout_s
        ):
            receive_ns = round(datagram.timestamp_monotonic_s * 1_000_000_000)
            packet = decode_packet(datagram.payload)
            stamp = self.clock.stamp(
                receive_ns,
                source_clock_ns=receive_ns,
                game_clock_ms=packet.timestamp_ms,
            )
            yield TelemetryInput(
                bytes(datagram.payload),
                stamp.session_ns,
                datagram.source,
                stamp.source_clock_ns,
                stamp.game_clock_ms,
            )

    def close(self) -> None:
        self.receiver.close()


class CaptureCoordinator:
    """Coordinate source iterators and one serialized durable recorder.

    Camera/controller queues explicitly drop oldest samples under overload;
    telemetry rejects overload and faults the session because it is lossless.
    """

    def __init__(
        self,
        recorder: SessionRecorder,
        camera: Any,
        controller: Any,
        telemetry: Any,
        *,
        max_seconds: float,
        max_frames: int,
        camera_capacity: int = 8,
        controller_capacity: int = 32,
        telemetry_capacity: int = 256,
        receive_timeout_s: float = 0.25,
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
        if receive_timeout_s <= 0:
            raise ValueError("receive_timeout_s must be positive")
        self.recorder, self.camera, self.controller, self.telemetry = (
            recorder,
            camera,
            controller,
            telemetry,
        )
        self.max_seconds, self.max_frames = float(max_seconds), max_frames
        self.receive_timeout_s, self._clock = receive_timeout_s, monotonic_ns
        self._camera_q: queue.Queue[Any] = queue.Queue(maxsize=camera_capacity)
        self._controller_q: queue.Queue[Any] = queue.Queue(maxsize=controller_capacity)
        self._telemetry_q: queue.Queue[Any] = queue.Queue(maxsize=telemetry_capacity)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._started = False
        self._fault: BaseException | None = None
        self._frames = 0
        self.health = recorder.health
        self._origin_ns = 0

    @property
    def fault(self) -> BaseException | None:
        return self._fault

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _put_drop_oldest(self, target: queue.Queue[Any], value: Any) -> None:
        try:
            target.put_nowait(value)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            self.recorder.record_drop()
            target.put_nowait(value)

    def _camera_worker(self) -> None:
        try:
            for item in self.camera.frames(max_frames=self.max_frames):
                if self._stop.is_set():
                    break
                self._put_drop_oldest(self._camera_q, item)
                if self._clock() - self._origin_ns >= int(self.max_seconds * 1e9):
                    self._stop.set()
                    break
        except BaseException as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._fault = exc
                self._stop.set()
        finally:
            try:
                self.camera.close()
            except BaseException as exc:
                with self._lock:
                    if self._fault is None:
                        self._fault = exc
                self._stop.set()

    def _controller_worker(self) -> None:
        try:
            for item in self.controller.samples(max_samples=None):
                if self._stop.is_set():
                    break
                self._put_drop_oldest(self._controller_q, item)
        except ControllerDisconnected:
            self.recorder.record_disconnect()
            if not self._stop.is_set():
                self._controller_worker_disconnect()
        except BaseException as exc:
            if self._stop.is_set():
                return
            with self._lock:
                self._fault = exc
            self._stop.set()

    def _controller_worker_disconnect(self) -> None:
        """Persist disconnect diagnostics and keep the other streams alive."""
        self.recorder.record_stale()

    def _telemetry_worker(self) -> None:
        try:
            source = self.telemetry.records(
                max_packets=None, timeout_s=self.receive_timeout_s
            )
            for item in source:
                if self._stop.is_set():
                    break
                if not isinstance(item, TelemetryInput):
                    receive_ns = int(
                        round(float(item.timestamp_monotonic_s) * 1_000_000_000)
                    )
                    item = TelemetryInput(
                        item.payload,
                        max(0, receive_ns - self._origin_ns),
                        item.source,
                        receive_ns,
                        None,
                    )
                try:
                    self._telemetry_q.put_nowait(item)
                except queue.Full as exc:
                    self.recorder.record_writer_fault()
                    with self._lock:
                        self._fault = QueueOverloadError("telemetry queue is full")
                    raise QueueOverloadError("telemetry queue is full") from exc
        except BaseException as exc:
            if self._stop.is_set() and self._fault is None:
                return
            with self._lock:
                if self._fault is None:
                    self._fault = exc
            self._stop.set()

    def _remember_fault(self, exc: BaseException) -> None:
        with self._lock:
            if self._fault is None:
                self._fault = exc

    def _write_one(self, item: Any, kind: str) -> None:
        if kind == "frame":
            if self._frames >= self.max_frames:
                self._stop.set()
                return
            self.recorder.append_frame(item)
            self._frames += 1
        elif kind == "controller":
            self.recorder.append_controller(item)
        else:
            if isinstance(item, TelemetryInput):
                value = item
            else:
                value = TelemetryInput(
                    item.payload,
                    int(getattr(item, "session_ns", 0)),
                    item.source,
                    getattr(item, "source_clock_ns", None),
                    getattr(item, "game_clock_ms", None),
                )
            self.recorder.append_telemetry(
                value.payload,
                value.session_ns,
                value.source,
                value.source_clock_ns,
                value.game_clock_ms,
            )

    def _drain(self) -> None:
        queues = (
            (self._camera_q, "frame"),
            (self._controller_q, "controller"),
            (self._telemetry_q, "telemetry"),
        )
        while True:
            progressed = False
            for selected, kind in queues:
                try:
                    item = selected.get_nowait()
                except queue.Empty:
                    continue
                self._write_one(item, kind)
                progressed = True
            if not progressed:
                return

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("coordinator already started")
            self._started, self._origin_ns = True, self._clock()
            self._threads = [
                threading.Thread(target=self._camera_worker, name="fh4-camera"),
                threading.Thread(target=self._controller_worker, name="fh4-controller"),
                threading.Thread(target=self._telemetry_worker, name="fh4-telemetry"),
            ]
            for thread in self._threads:
                thread.start()

    def run(self) -> SessionManifest:
        interrupted: BaseException | None = None
        try:
            self.start()
            deadline = self._origin_ns + int(self.max_seconds * 1e9)
            while not self._stop.is_set() and self._clock() < deadline:
                self._drain()
                if self._threads and all(
                    not thread.is_alive() for thread in self._threads
                ):
                    self._stop.set()
                    break
                time.sleep(0.001)
        except BaseException as exc:
            self._remember_fault(exc)
            interrupted = exc
        finally:
            self.stop()
            try:
                self.join()
            except Exception as exc:
                self._remember_fault(exc)
            try:
                self._drain()
            except Exception as exc:
                self._remember_fault(exc)
        if self._fault is not None:
            try:
                self.recorder.close()
            except BaseException as close_exc:
                self._fault.add_note(f"recorder cleanup failed: {close_exc!r}")
            if interrupted is not None and not isinstance(interrupted, Exception):
                raise interrupted
            raise RuntimeError("capture coordinator fault") from self._fault
        try:
            return self.recorder.finalize()
        except BaseException as exc:
            try:
                self.recorder.close()
            except BaseException as close_exc:
                exc.add_note(f"recorder cleanup failed: {close_exc!r}")
            raise

    def stop(self) -> None:
        self._stop.set()
        for source in (self.camera, self.controller, self.telemetry):
            close = getattr(source, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except BaseException as exc:
                self._remember_fault(exc)

    def join(self, timeout_s: float = 2.0) -> None:
        deadline = time.monotonic() + timeout_s
        for thread in self._threads:
            if thread.ident is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        if any(thread.is_alive() for thread in self._threads):
            raise TimeoutError("capture source did not stop before join deadline")


__all__ = ["CaptureCoordinator", "SessionTelemetrySource", "TelemetryInput"]
