"""Lazy fixed-profile DXGI camera capture and owned JPEG encoding."""
# ruff: noqa: E501

from __future__ import annotations

import gc
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from ..sync import SessionClock, TimelineStamp


@dataclass(frozen=True, slots=True)
class CameraProfile:
    source_width: int = 2560
    source_height: int = 1440
    output_width: int = 960
    output_height: int = 540
    fps: int = 30
    jpeg_quality: int = 85
    output_index: int = 0

    def __post_init__(self) -> None:
        for name in (
            "source_width",
            "source_height",
            "output_width",
            "output_height",
            "fps",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.source_width != 2560 or self.source_height != 1440:
            raise ValueError("source must be the fixed 2560x1440 profile")
        if self.output_width != 960 or self.output_height != 540 or self.fps != 30:
            raise ValueError("output must be fixed 960x540 at 30 FPS")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        if isinstance(self.output_index, bool) or self.output_index < 0:
            raise ValueError("output_index must be non-negative")

    @property
    def region(self) -> tuple[int, int, int, int]:
        return (0, 0, self.source_width, self.source_height)

    def to_mapping(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class CameraFrame:
    """An owned, encoded frame; source and session clocks are retained."""

    frame_id: int
    timestamp: TimelineStamp
    jpeg: bytes
    width: int = 960
    height: int = 540

    @property
    def session_ns(self) -> int:
        return self.timestamp.session_ns

    @property
    def monotonic_ns(self) -> int:
        return self.timestamp.monotonic_ns

    @property
    def source_clock_ns(self) -> int:
        if self.timestamp.source_clock_ns is not None:
            return self.timestamp.source_clock_ns
        return self.timestamp.monotonic_ns

    def __post_init__(self) -> None:
        if self.frame_id < 0 or not isinstance(self.frame_id, int):
            raise ValueError("frame_id must be non-negative")
        if not isinstance(self.timestamp, TimelineStamp):
            raise ValueError("timestamp must be TimelineStamp")
        if not isinstance(self.jpeg, bytes) or not self.jpeg:
            raise ValueError("jpeg must be non-empty bytes")
        if (self.width, self.height) != (960, 540):
            raise ValueError("encoded frame must be 960x540")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read JPEG dimensions without trusting backend-owned frame memory."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("encoded frame is not a JPEG")
    index = 2
    while index + 9 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in (0xD8, 0xD9):
            continue
        if index + 2 > len(data):
            break
        size = int.from_bytes(data[index : index + 2], "big")
        if size < 2 or index + size > len(data):
            break
        if (
            marker in range(0xC0, 0xC4)
            or marker in range(0xC5, 0xC8)
            or marker in range(0xC9, 0xCC)
            or marker in range(0xCD, 0xD0)
        ):
            if size < 7:
                break
            return (
                int.from_bytes(data[index + 5 : index + 7], "big"),
                int.from_bytes(data[index + 3 : index + 5], "big"),
            )
        index += size
    raise ValueError("JPEG dimensions are missing")


def _validate_jpeg(data: bytes, profile: CameraProfile) -> bytes:
    width, height = _jpeg_dimensions(data)
    if (width, height) != (profile.output_width, profile.output_height):
        raise ValueError("JPEG dimensions must be exactly 960x540")
    return data


def encode_jpeg(  # noqa: B008
    frame: Any,
    profile: CameraProfile = CameraProfile(),  # noqa: B008
) -> bytes:
    """Copy a DXcam frame and encode an owned, dimension-validated JPEG."""
    if isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError("raw bytes are not an accepted camera frame")
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Windows dependency path
        raise RuntimeError("camera encoding requires numpy and Pillow") from exc
    owned = np.array(frame, copy=True, order="C")
    # Decode the contiguous DXcam BGR buffer directly. Reversing the channel
    # view creates a negative-stride copy inside Pillow and cannot sustain 30 FPS.
    if getattr(owned, "ndim", 0) == 3 and owned.shape[2] == 3:
        height, width = owned.shape[:2]
        image = Image.frombuffer(
            "RGB", (width, height), owned, "raw", "BGR", 0, 1
        )
    else:
        image = Image.fromarray(owned)
    image = image.resize(
        (profile.output_width, profile.output_height), Image.Resampling.BOX
    )
    import io

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=profile.jpeg_quality, optimize=False)
    return _validate_jpeg(output.getvalue(), profile)


class CameraSource(Protocol):
    def frames(self, *, max_frames: int | None = None) -> Iterator[CameraFrame]: ...
    def close(self) -> None: ...


class DXcamCamera:
    """DXGI camera adapter. Device creation is deferred until ``frames``."""

    def __init__(
        self,
        clock: SessionClock,
        *,
        profile: CameraProfile = CameraProfile(),  # noqa: B008
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.clock = clock
        self.profile = profile
        self._monotonic_ns = monotonic_ns
        self._camera: Any = None
        self._owner_thread_id: int | None = None
        self._stop_requested = threading.Event()
        self._closed = False

    def _ensure_camera(self) -> Any:
        if self._closed:
            raise RuntimeError("camera is closed")
        if self._camera is None:
            try:
                import dxcam  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - Windows dependency path
                raise RuntimeError(
                    "DXcam 0.3.0 is required for camera capture"
                ) from exc
            self._camera = dxcam.create(
                output_idx=self.profile.output_index,
                output_color="BGR",
                backend="dxgi",
                processor_backend="numpy",
                max_buffer_len=8,
            )
            self._owner_thread_id = threading.get_ident()
            self._stop_requested.clear()
            self._camera.start(
                region=self.profile.region, target_fps=self.profile.fps, video_mode=True
            )
        elif self._owner_thread_id != threading.get_ident():
            raise RuntimeError("DXcam must be used from its owning thread")
        return self._camera

    def frames(self, *, max_frames: int | None = None) -> Iterator[CameraFrame]:
        if max_frames is not None and (isinstance(max_frames, bool) or max_frames <= 0):
            raise ValueError("max_frames must be positive")
        camera = self._ensure_camera()
        captured_count = 0
        pending: deque[tuple[int, TimelineStamp, Future[bytes]]] = deque()
        with ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="fh4-jpeg"
        ) as encoder:
            while (
                not self._stop_requested.is_set()
                and (max_frames is None or captured_count < max_frames)
            ):
                captured = camera.get_latest_frame(with_timestamp=True)
                if captured is None:
                    continue
                if not isinstance(captured, tuple) or len(captured) != 2:
                    raise RuntimeError("DXcam did not return a frame timestamp")
                raw, source_timestamp_s = captured
                mono = self._monotonic_ns()
                source_ns = round(float(source_timestamp_s) * 1_000_000_000)
                stamp = self.clock.stamp(mono, source_clock_ns=source_ns)
                future = encoder.submit(encode_jpeg, raw, self.profile)
                pending.append((captured_count, stamp, future))
                captured_count += 1
                if len(pending) >= 8:
                    frame_id, frame_stamp, encoded = pending.popleft()
                    yield CameraFrame(frame_id, frame_stamp, encoded.result())
            while pending:
                frame_id, frame_stamp, encoded = pending.popleft()
                yield CameraFrame(frame_id, frame_stamp, encoded.result())

    def close(self) -> None:
        self._stop_requested.set()
        if self._camera is None:
            self._closed = True
            return
        if self._owner_thread_id != threading.get_ident():
            return
        camera = self._camera
        try:
            stop = getattr(camera, "stop", None)
            if callable(stop):
                stop()
        finally:
            # DXcam 0.3.0 explicitly calls Release() on comtypes-owned
            # pointers, whose finalizers then release the same pointers again.
            # Let comtypes perform the single owned release instead.
            if type(camera).__module__.split(".", 1)[0] == "dxcam":
                camera._is_released = True
            else:
                release = getattr(camera, "release", None)
                if callable(release):
                    release()
            self._camera = None
            self._owner_thread_id = None
            self._closed = True
            del camera
            if "dxcam" in sys.modules:
                gc.collect()

    def __enter__(self) -> DXcamCamera:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


FixedCameraProfile = CameraProfile
FH4Camera = DXcamCamera

__all__ = [
    "CameraFrame",
    "CameraProfile",
    "CameraSource",
    "DXcamCamera",
    "FH4Camera",
    "FixedCameraProfile",
    "encode_jpeg",
]
