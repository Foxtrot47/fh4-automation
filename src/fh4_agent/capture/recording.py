"""Crash-safe sequential session storage and partial-tail recovery."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import heapq
import json
import os
import struct
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from ..contracts import BenchmarkIdentity, SessionMetadata, benchmark_digest
from ..sync import HealthCounters
from ..telemetry import PACKET_LENGTHS

FRAME_MAGIC = b"FH4JPG01"
_FRAME_HEADER = struct.Struct("<8sHI")
_FRAME_RECORD_V1 = struct.Struct("<4sIQI")
_FRAME_RECORD = struct.Struct("<4sIQQ")  # marker, jpeg length, session ns, source ns
_FRAME_VERSION = 2
_FRAME_CRC = struct.Struct("<I")
_FRAME_MARKER = b"FRM1"
MAX_JPEG_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_JSON_LINE_BYTES = 4 * 1024 * 1024
REQUIRED_SESSION_FILES = frozenset(
    {
        "frames.fh4jpg",
        "controller.jsonl",
        "telemetry.fh4cap",
        "telemetry.jsonl",
        "health.jsonl",
    }
)
REQUIRED_HEALTH_FIELDS = frozenset(HealthCounters.__dataclass_fields__)


class RecordingError(ValueError):
    """Malformed, incomplete, or incompatible session recording."""


def profile_digest(profile: object) -> str:
    values = (
        profile.to_mapping()
        if hasattr(profile, "to_mapping")
        else asdict(cast(Any, profile))
    )
    canonical = json.dumps(
        values, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fsync(file: Any) -> None:
    file.flush()
    os.fsync(file.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class FrameChunkWriter:
    """Append-only JPEG chunks with length bounds, CRC, and durable writes."""

    def __init__(
        self,
        path: str | Path,
        *,
        metadata: Mapping[str, object] | None = None,
        fsync_each_record: bool = True,
    ) -> None:
        if not isinstance(fsync_each_record, bool):
            raise ValueError("fsync_each_record must be a boolean")
        self._fsync_each_record = fsync_each_record
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = json.dumps(
            dict(metadata or {}), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        self._file = self.path.open("wb")
        if len(doc) > MAX_METADATA_BYTES:
            raise RecordingError("frame metadata exceeds configured bound")
        self._file.write(_FRAME_HEADER.pack(FRAME_MAGIC, _FRAME_VERSION, len(doc)))
        self._file.write(doc)
        self._file.write(_FRAME_CRC.pack(zlib.crc32(doc) & 0xFFFFFFFF))
        _fsync(self._file)
        self.count = 0
        self._closed = False

    def append(
        self, jpeg: bytes, session_ns: int, source_clock_ns: int | None = None
    ) -> None:
        if self._closed:
            raise RecordingError("frame writer is closed")
        if not isinstance(jpeg, bytes) or not 1 <= len(jpeg) <= MAX_JPEG_BYTES:
            raise RecordingError("JPEG length is outside the configured bound")
        if (
            isinstance(session_ns, bool)
            or not isinstance(session_ns, int)
            or not 0 <= session_ns <= 0xFFFFFFFFFFFFFFFF
        ):
            raise RecordingError("session_ns must fit uint64")
        source = 0 if source_clock_ns is None else source_clock_ns
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or not 0 <= source <= 0xFFFFFFFFFFFFFFFF
        ):
            raise RecordingError("source_clock_ns must fit uint64")
        prefix = _FRAME_RECORD.pack(_FRAME_MARKER, len(jpeg), session_ns, source)
        body = prefix + jpeg
        self._file.write(body + _FRAME_CRC.pack(zlib.crc32(body) & 0xFFFFFFFF))
        self._file.flush()
        if self._fsync_each_record:
            os.fsync(self._file.fileno())
        self.count += 1

    def close(self) -> None:
        if not self._closed:
            _fsync(self._file)
            self._file.close()
            self._closed = True

    def __enter__(self) -> FrameChunkWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class FrameChunk:
    jpeg: bytes
    session_ns: int
    source_clock_ns: int


def read_frame_chunks(
    path: str | Path, *, recover_tail: bool = False
) -> Iterator[FrameChunk]:
    file_path = Path(path)
    with file_path.open("r+b" if recover_tail else "rb") as file:
        raw = file.read(_FRAME_HEADER.size)
        if len(raw) != _FRAME_HEADER.size:
            raise RecordingError("frame chunk header is truncated")
        magic, version, metadata_len = _FRAME_HEADER.unpack(raw)
        if magic != FRAME_MAGIC or version not in {1, _FRAME_VERSION}:
            raise RecordingError("frame chunk header is invalid")
        record_struct = _FRAME_RECORD_V1 if version == 1 else _FRAME_RECORD
        if metadata_len > MAX_METADATA_BYTES:
            raise RecordingError("frame metadata exceeds configured bound")
        metadata = file.read(metadata_len)
        crc = file.read(_FRAME_CRC.size)
        if (
            len(metadata) != metadata_len
            or len(crc) != _FRAME_CRC.size
            or zlib.crc32(metadata) & 0xFFFFFFFF != _FRAME_CRC.unpack(crc)[0]
        ):
            raise RecordingError("frame chunk metadata is corrupt")
        valid_end = file.tell()
        last_session_ns: int | None = None
        last_source_ns: int | None = None
        while True:
            prefix = file.read(record_struct.size)
            if not prefix:
                return
            if len(prefix) != record_struct.size:
                if recover_tail:
                    file.truncate(valid_end)
                    return
                raise RecordingError("frame chunk tail is truncated")
            marker, length, session_ns, source_ns = record_struct.unpack(prefix)
            if marker != _FRAME_MARKER or length == 0 or length > MAX_JPEG_BYTES:
                raise RecordingError("frame chunk record is invalid")
            if last_session_ns is not None and session_ns < last_session_ns:
                raise RecordingError("frame session timestamps decrease")
            if last_source_ns is not None and source_ns < last_source_ns:
                raise RecordingError("frame source timestamps decrease")
            jpeg = file.read(length)
            crc = file.read(_FRAME_CRC.size)
            if len(jpeg) != length or len(crc) != _FRAME_CRC.size:
                if recover_tail:
                    file.truncate(valid_end)
                    return
                raise RecordingError("frame chunk tail is truncated")
            body = prefix + jpeg
            if zlib.crc32(body) & 0xFFFFFFFF != _FRAME_CRC.unpack(crc)[0]:
                if recover_tail:
                    file.truncate(valid_end)
                    return
                raise RecordingError("frame chunk CRC mismatch")
            valid_end = file.tell()
            last_session_ns = session_ns
            last_source_ns = source_ns
            yield FrameChunk(jpeg, session_ns, source_ns)


@dataclass(frozen=True, slots=True)
class SessionManifest:
    session_id: str
    benchmark_digest: str
    profile_digest: str
    files: dict[str, str]
    health: dict[str, int]
    complete: bool = True
    metadata: dict[str, object] | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": 1,
            "session_id": self.session_id,
            "benchmark_digest": self.benchmark_digest,
            "profile_digest": self.profile_digest,
            "files": dict(sorted(self.files.items())),
            "health": dict(sorted(self.health.items())),
            "complete": self.complete,
            "metadata": self.metadata or {},
        }


def validate_manifest(
    path: str | Path, benchmark: BenchmarkIdentity, profile: object
) -> SessionManifest:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != 1:
            raise ValueError
        if set(document) != {
            "schema",
            "session_id",
            "benchmark_digest",
            "profile_digest",
            "files",
            "health",
            "complete",
            "metadata",
        }:
            raise ValueError("manifest contains unknown or missing fields")
        if (
            not isinstance(document["complete"], bool)
            or not isinstance(document["files"], dict)
            or not isinstance(document["health"], dict)
            or not isinstance(document["metadata"], dict)
        ):
            raise ValueError("manifest fields have invalid types")
        if any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in document["health"].items()
        ):
            raise ValueError("manifest health counters have invalid types")
        result = SessionManifest(
            str(document["session_id"]),
            str(document["benchmark_digest"]),
            str(document["profile_digest"]),
            dict(document["files"]),
            dict(document["health"]),
            document["complete"],
            document["metadata"],
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RecordingError("manifest is malformed") from exc
    expected_benchmark_digest = benchmark_digest(benchmark)
    expected_profile_digest = profile_digest(profile)
    if result.benchmark_digest != expected_benchmark_digest:
        raise RecordingError("manifest benchmark digest does not match")
    if result.profile_digest != expected_profile_digest:
        raise RecordingError("manifest capture profile digest does not match")
    try:
        metadata = SessionMetadata.from_mapping(result.metadata)
    except (TypeError, ValueError) as exc:
        raise RecordingError("manifest metadata is malformed") from exc
    if (
        metadata.session_id != result.session_id
        or metadata.benchmark != benchmark
        or metadata.config_digest != expected_benchmark_digest
        or metadata.profile_digest != expected_profile_digest
    ):
        raise RecordingError("manifest metadata does not match manifest identity")
    missing_files = REQUIRED_SESSION_FILES - set(result.files)
    if missing_files:
        raise RecordingError(
            "manifest is missing required stream(s): "
            + ", ".join(sorted(missing_files))
        )
    if set(result.health) != REQUIRED_HEALTH_FIELDS or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in result.health.values()
    ):
        raise RecordingError("manifest health counters are invalid")
    for name, expected in result.files.items():
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or Path(name).is_absolute()
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise RecordingError("manifest file entry is invalid")
        file_path = Path(path).parent / name
        if not file_path.is_file():
            raise RecordingError(f"manifest file is missing: {name}")
        actual = _sha256_file(file_path)
        if actual != expected:
            raise RecordingError(f"manifest file checksum mismatch: {name}")
    if not result.complete:
        raise RecordingError("session manifest is incomplete")
    return result


class SessionRecorder:
    """Own all stream writers and publish a manifest only after final fsync."""

    def __init__(
        self,
        directory: str | Path,
        *,
        session_id: str,
        benchmark: BenchmarkIdentity,
        profile: object,
        health: HealthCounters | None = None,
        metadata: SessionMetadata | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.session_id = session_id
        self.benchmark = benchmark
        self.profile = profile
        self.metadata = metadata or SessionMetadata.create(
            benchmark,
            game_build="unknown",
            started_monotonic_s=0.0,
            session_id=session_id,
            profile_digest=profile_digest(profile),
        )
        if (
            self.metadata.session_id != session_id
            or self.metadata.benchmark != benchmark
        ):
            raise RecordingError("session metadata does not match recorder identity")
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RecordingError("session directory already exists") from exc
        self.health = health or HealthCounters()
        self._lock = __import__("threading").RLock()
        self._closed = False
        opened: list[Any] = []
        try:
            self.frames = FrameChunkWriter(
                self.directory / "frames.fh4jpg",
                metadata={"profile_digest": profile_digest(profile)},
                fsync_each_record=False,
            )
            opened.append(self.frames)
            self._controller = (self.directory / "controller.jsonl").open("xb")
            opened.append(self._controller)
            from .telemetry import CaptureWriter

            self._telemetry: Any = CaptureWriter(
                self.directory / "telemetry.fh4cap",
                metadata={"session_id": self.session_id},
                fsync_each_record=False,
            )
            opened.append(self._telemetry)
            self._telemetry_log = (self.directory / "telemetry.jsonl").open("xb")
            opened.append(self._telemetry_log)
            self._health_log = (self.directory / "health.jsonl").open("xb")
            opened.append(self._health_log)
        except BaseException as exc:
            for resource in reversed(opened):
                try:
                    resource.close()
                except BaseException as close_exc:
                    exc.add_note(f"partial recorder cleanup failed: {close_exc!r}")
            self._closed = True
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RecordingError("session recorder is closed")

    def _health_event(self, field: str, count: int) -> None:
        self._ensure_open()
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("health count must be a positive integer")
        setattr(self.health, field, getattr(self.health, field) + count)
        self._json_append(self._health_log, {"event": field, "count": count})

    def record_drop(self, count: int = 1) -> None:
        self._health_event("dropped", count)

    def record_stale(self, count: int = 1) -> None:
        self._health_event("stale", count)

    def record_disconnect(self, count: int = 1) -> None:
        self._health_event("disconnects", count)

    def record_writer_fault(self, count: int = 1) -> None:
        self._health_event("writer_faults", count)

    def _json_append(self, file: Any, value: Mapping[str, object]) -> None:
        encoded = (
            json.dumps(
                dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            + b"\n"
        )
        if len(encoded) > MAX_JSON_LINE_BYTES:
            raise RecordingError("JSON record exceeds configured bound")
        file.write(encoded)
        file.flush()

    def append_frame(self, frame: Any) -> None:
        self._ensure_open()
        stamp = frame.timestamp
        from .camera import CameraProfile, _validate_jpeg

        jpeg = _validate_jpeg(bytes(frame.jpeg), CameraProfile())
        self.frames.append(jpeg, stamp.session_ns, stamp.source_clock_ns)

    def append_controller(self, sample: Any) -> None:
        self._ensure_open()
        session_value = getattr(sample, "session_ns", None)
        if (
            isinstance(session_value, bool)
            or not isinstance(session_value, int)
            or session_value < 0
        ):
            raise RecordingError("controller sample must carry integer session_ns")
        self._json_append(
            self._controller,
            {"session_ns": session_value, "sample": sample.to_mapping()},
        )

    def append_telemetry(
        self,
        payload: bytes,
        session_ns: int,
        source: tuple[str, int],
        source_clock_ns: int | None = None,
        game_clock_ms: int | None = None,
    ) -> None:
        self._ensure_open()
        if (
            isinstance(session_ns, bool)
            or not isinstance(session_ns, int)
            or session_ns < 0
        ):
            raise RecordingError("session_ns must be a non-negative integer")
        for value, name in (
            (source_clock_ns, "source_clock_ns"),
            (game_clock_ms, "game_clock_ms"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise RecordingError(f"{name} must be a non-negative integer")
        if len(payload) not in PACKET_LENGTHS:
            raise RecordingError("session telemetry must be 323 or 324 bytes")
        # The binary stream preserves exact datagrams; the JSONL sidecar retains
        # integer alignment and source/game clocks without lossy conversion.
        self._telemetry.append_datagram_ns(payload, session_ns, source)
        self._json_append(
            self._telemetry_log,
            {
                "session_ns": session_ns,
                "source_clock_ns": source_clock_ns,
                "game_clock_ms": game_clock_ms,
                "source": list(source),
                "payload_hex": bytes(payload).hex(),
            },
        )

    def finalize(self) -> SessionManifest:
        if self._closed:
            raise RecordingError("session recorder is closed")
        self.frames.close()
        _fsync(self._controller)
        self._controller.close()
        _fsync(self._telemetry_log)
        self._telemetry_log.close()
        _fsync(self._health_log)
        self._health_log.close()
        self._telemetry.close()
        files = {}
        for path in sorted(self.directory.iterdir()):
            if path.name == "manifest.json" or path.name.endswith(".tmp"):
                continue
            files[path.name] = _sha256_file(path)
        manifest = SessionManifest(
            self.session_id,
            benchmark_digest(self.benchmark),
            profile_digest(self.profile),
            files,
            self.health.to_mapping(),
            metadata=self.metadata.to_mapping(),
        )
        temporary = self.directory / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(manifest.to_mapping(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with temporary.open("r+b") as file:
            _fsync(file)
        manifest_path = self.directory / "manifest.json"
        os.replace(temporary, manifest_path)
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Windows may not expose directory handles; file and rename
            # durability above still provides recoverability.
            pass
        self._closed = True
        return manifest

    def close(self) -> None:
        if not self._closed:
            self.frames.close()
            _fsync(self._controller)
            self._controller.close()
            _fsync(self._telemetry_log)
            self._telemetry_log.close()
            _fsync(self._health_log)
            self._health_log.close()
            self._telemetry.close()
            self._closed = True


def _recover_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    valid = 0
    with path.open("rb") as file:
        while True:
            start = file.tell()
            line = file.readline()
            if not line:
                break
            if len(line) > MAX_JSON_LINE_BYTES or not line.endswith(b"\n"):
                file.close()
                with path.open("r+b") as trunc:
                    trunc.truncate(start)
                break
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                file.close()
                with path.open("r+b") as trunc:
                    trunc.truncate(start)
                break
            valid += 1
    return valid


def _replay_jsonl(path: Path, stream: str) -> Iterator[dict[str, object]]:
    last_session_ns: int | None = None
    with path.open("rb") as file:
        for raw in file:
            if len(raw) > MAX_JSON_LINE_BYTES or not raw.endswith(b"\n"):
                raise RecordingError(f"{stream} JSONL record is truncated or oversized")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RecordingError(f"{stream} JSONL record is malformed") from exc
            if not isinstance(value, dict):
                raise RecordingError(f"{stream} JSONL record must be an object")
            session_ns = value.get("session_ns", 0)
            if (
                isinstance(session_ns, bool)
                or not isinstance(session_ns, int)
                or session_ns < 0
            ):
                raise RecordingError(f"{stream} session timestamp is invalid")
            if last_session_ns is not None and session_ns < last_session_ns:
                raise RecordingError(f"{stream} session timestamps decrease")
            last_session_ns = session_ns
            yield {**value, "stream": stream}


def _replay_key(value: Mapping[str, object]) -> tuple[int, str]:
    session_ns = value.get("session_ns", 0)
    if isinstance(session_ns, bool) or not isinstance(session_ns, int):
        raise RecordingError("replay event session timestamp is invalid")
    return session_ns, str(value["stream"])


def replay_session(directory: str | Path) -> Iterator[dict[str, object]]:
    """Stream every session record in deterministic session-clock order."""
    root = Path(directory)
    streams: list[Iterator[dict[str, object]]] = []
    frames = root / "frames.fh4jpg"
    if frames.exists():
        streams.append(
            (
                {
                    "stream": "frame",
                    "index": index,
                    "session_ns": frame.session_ns,
                    "jpeg": frame.jpeg,
                }
                for index, frame in enumerate(read_frame_chunks(frames))
            )
        )
    for name, stream in (
        ("controller.jsonl", "controller"),
        ("telemetry.jsonl", "telemetry"),
        ("health.jsonl", "health"),
    ):
        path = root / name
        if path.exists():
            streams.append(_replay_jsonl(path, stream))
    yield from heapq.merge(*streams, key=_replay_key)


def recover_session(directory: str | Path) -> dict[str, int]:
    """Recover complete tails from every stream, without requiring a manifest."""
    root = Path(directory)
    result = {"frames": 0, "controller": 0, "telemetry": 0, "health": 0}
    frames = root / "frames.fh4jpg"
    if frames.exists():
        result["frames"] = sum(1 for _ in read_frame_chunks(frames, recover_tail=True))
    result["controller"] = _recover_jsonl(root / "controller.jsonl")
    result["telemetry"] = _recover_jsonl(root / "telemetry.jsonl")
    capture = root / "telemetry.fh4cap"
    if capture.exists():
        from .telemetry import recover_capture_tail

        recover_capture_tail(capture)
    result["health"] = _recover_jsonl(root / "health.jsonl")
    return result


def recover_partial_session(directory: str | Path) -> int:
    """Backward-compatible frame count; all streams are recovered as a side effect."""
    return recover_session(directory)["frames"]


__all__ = [
    "FRAME_MAGIC",
    "FrameChunk",
    "FrameChunkWriter",
    "MAX_JPEG_BYTES",
    "RecordingError",
    "SessionManifest",
    "SessionRecorder",
    "profile_digest",
    "read_frame_chunks",
    "recover_partial_session",
    "recover_session",
    "replay_session",
    "validate_manifest",
]
