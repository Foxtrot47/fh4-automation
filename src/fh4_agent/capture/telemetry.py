"""Versioned, append-only raw UDP datagram capture and deterministic replay."""
# ruff: noqa: E501

from __future__ import annotations

import json
import math
import os
import struct
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAGIC = b"FH4CAP01"
FORMAT_VERSION = 1
_HEADER = struct.Struct("<8sHI")
_U32 = struct.Struct("<I")
_RECORD_PREFIX = struct.Struct("<4sIdH")
_RECORD_NS_PREFIX = struct.Struct("<4sIQH")
_RECORD_MAGIC = b"DG01"
_RECORD_NS_MAGIC = b"DN01"
_FH4_PACKET_LENGTHS = frozenset({323, 324})
_MAX_UDP_DATAGRAM_SIZE = 65507
_MAX_SOURCE_BYTES = 65535
_MAX_METADATA_BYTES = 1024 * 1024


def _parse_metadata(document: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite metadata constant {value}")

    try:
        value = json.loads(document, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CaptureError("capture metadata is malformed") from exc
    if not isinstance(value, dict):
        raise CaptureError("capture metadata must be an object")
    return value


class CaptureError(ValueError):
    """Raised for malformed, corrupt, truncated, or incompatible captures."""


class CaptureLimitReached(CaptureError):
    """Raised when a bounded writer reaches its configured limit."""


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    payload: bytes
    timestamp_monotonic_s: float
    source: tuple[str, int]
    timestamp_monotonic_ns: int | None = None

    @property
    def timestamp(self) -> float:
        return self.timestamp_monotonic_s

    @property
    def session_ns(self) -> int:
        if self.timestamp_monotonic_ns is not None:
            return self.timestamp_monotonic_ns
        return round(self.timestamp_monotonic_s * 1_000_000_000)


class CaptureWriter:
    """Write exact datagrams in a durable append-only binary stream."""

    def __init__(
        self,
        path: str | Path,
        *,
        metadata: Mapping[str, object] | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
        append: bool = False,
        fsync_each_record: bool = True,
    ) -> None:
        self.path = Path(path)
        self.max_records = max_records
        self.max_bytes = max_bytes
        if not isinstance(fsync_each_record, bool):
            raise ValueError("fsync_each_record must be a boolean")
        self._fsync_each_record = fsync_each_record
        if max_records is not None and (
            not isinstance(max_records, int)
            or isinstance(max_records, bool)
            or max_records < 0
        ):
            raise ValueError("max_records must be a non-negative integer")
        if max_bytes is not None and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        ):
            raise ValueError("max_bytes must be a non-negative integer")
        try:
            document = json.dumps(
                dict(metadata or {}),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CaptureError("capture metadata is not valid JSON") from exc
        if len(document) > _MAX_METADATA_BYTES:
            raise CaptureError("capture metadata exceeds configured bound")
        header = _HEADER.pack(MAGIC, FORMAT_VERSION, len(document))
        header += document + _U32.pack(zlib.crc32(document) & 0xFFFFFFFF)
        if max_bytes is not None and max_bytes < len(header):
            raise CaptureError("max_bytes must include the capture header")
        self._file: BinaryIO
        self.count = 0
        self._last_timestamp: float | None = None
        self._last_timestamp_ns: int | None = None
        self._closed = False
        if append and self.path.exists():
            # Validate using a closed read handle before opening for append.
            existing = CaptureReader(self.path).read_all()
            if max_records is not None and len(existing) > max_records:
                raise CaptureError("existing capture exceeds max_records")
            self.count = len(existing)
            self._last_timestamp = (
                existing[-1].timestamp_monotonic_s if existing else None
            )
            self._last_timestamp_ns = existing[-1].session_ns if existing else None
            self._file = self.path.open("r+b")
            self._file.seek(0, 2)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("xb")
            try:
                self._file.write(header)
                self._file.flush()
                os.fsync(self._file.fileno())
            except BaseException:
                self._file.close()
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise

    @staticmethod
    def _validate_header(file: BinaryIO) -> tuple[int, int]:
        file.seek(0)
        raw = file.read(_HEADER.size)
        if len(raw) != _HEADER.size:
            raise CaptureError("capture header is truncated")
        magic, version, metadata_length = _HEADER.unpack(raw)
        if magic != MAGIC:
            raise CaptureError("capture magic is invalid")
        if version != FORMAT_VERSION:
            raise CaptureError(f"unsupported capture version {version}")
        if metadata_length > _MAX_METADATA_BYTES:
            raise CaptureError("capture metadata exceeds configured bound")
        metadata = file.read(metadata_length)
        checksum = file.read(_U32.size)
        if len(metadata) != metadata_length or len(checksum) != _U32.size:
            raise CaptureError("capture header is truncated")
        if zlib.crc32(metadata) & 0xFFFFFFFF != _U32.unpack(checksum)[0]:
            raise CaptureError("capture header checksum mismatch")
        _parse_metadata(metadata)
        return _HEADER.size + metadata_length + _U32.size, metadata_length

    def append(self, record: CaptureRecord) -> None:
        if self._closed:
            raise CaptureError("capture writer is closed")
        if not isinstance(record, CaptureRecord):
            raise TypeError("record must be CaptureRecord")
        source_host, source_port = record.source
        if not math.isfinite(record.timestamp_monotonic_s):
            raise CaptureError("record timestamp must be finite")
        if not isinstance(source_port, int) or not 0 <= source_port <= 65535:
            raise CaptureError("source port must be between 0 and 65535")
        source = str(source_host).encode("utf-8")
        if len(source) > _MAX_SOURCE_BYTES:
            raise CaptureError("source address is too long")
        if not record.payload or len(record.payload) > _MAX_UDP_DATAGRAM_SIZE:
            raise CaptureError("datagram payload must be between 1 and 65507 bytes")
        if (
            self._last_timestamp is not None
            and record.timestamp_monotonic_s < self._last_timestamp
        ):
            raise CaptureError("capture timestamps must be non-decreasing")
        prefix = _RECORD_PREFIX.pack(
            _RECORD_MAGIC,
            len(record.payload),
            float(record.timestamp_monotonic_s),
            int(source_port),
        )
        body = prefix + _U32.pack(len(source)) + source + bytes(record.payload)
        encoded = body + _U32.pack(zlib.crc32(body) & 0xFFFFFFFF)
        if self.max_records is not None and self.count >= self.max_records:
            raise CaptureLimitReached("capture max_records reached")
        if (
            self.max_bytes is not None
            and self._file.tell() + len(encoded) > self.max_bytes
        ):
            raise CaptureLimitReached("capture max_bytes reached")
        self._file.write(encoded)
        self._file.flush()
        if self._fsync_each_record:
            os.fsync(self._file.fileno())
        self.count += 1
        self._last_timestamp = record.timestamp_monotonic_s
        self._last_timestamp_ns = record.session_ns

    def append_datagram(
        self, payload: bytes, timestamp_monotonic_s: float, source: tuple[str, int]
    ) -> None:
        self.append(CaptureRecord(bytes(payload), timestamp_monotonic_s, source))

    def append_datagram_ns(
        self, payload: bytes, session_ns: int, source: tuple[str, int]
    ) -> None:
        """Append a datagram without converting the integer session clock."""
        if self._closed:
            raise CaptureError("capture writer is closed")
        if (
            isinstance(session_ns, bool)
            or not isinstance(session_ns, int)
            or not 0 <= session_ns <= 0xFFFFFFFFFFFFFFFF
        ):
            raise CaptureError("session_ns must fit uint64")
        host, port = source
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise CaptureError("source port must be between 0 and 65535")
        encoded_host = str(host).encode("utf-8")
        data = bytes(payload)
        if len(encoded_host) > _MAX_SOURCE_BYTES:
            raise CaptureError("source address is too long")
        if not 1 <= len(data) <= _MAX_UDP_DATAGRAM_SIZE:
            raise CaptureError("datagram payload must be between 1 and 65507 bytes")
        if self._last_timestamp_ns is not None and session_ns < self._last_timestamp_ns:
            raise CaptureError("capture timestamps must be non-decreasing")
        prefix = _RECORD_NS_PREFIX.pack(_RECORD_NS_MAGIC, len(data), session_ns, port)
        body = prefix + _U32.pack(len(encoded_host)) + encoded_host + data
        encoded = body + _U32.pack(zlib.crc32(body) & 0xFFFFFFFF)
        if self.max_records is not None and self.count >= self.max_records:
            raise CaptureLimitReached("capture max_records reached")
        if self.max_bytes is not None and self._file.tell() + len(encoded) > self.max_bytes:
            raise CaptureLimitReached("capture max_bytes reached")
        self._file.write(encoded)
        self._file.flush()
        if self._fsync_each_record:
            os.fsync(self._file.fileno())
        self.count += 1
        self._last_timestamp = session_ns / 1_000_000_000
        self._last_timestamp_ns = session_ns

    def close(self) -> None:
        if not self._closed:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._closed = True

    def __enter__(self) -> CaptureWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CaptureReader:
    """Validate and stream a capture; any partial/corrupt record is rejected."""

    def __init__(self, path: str | Path, *, strict_packet_lengths: bool = True) -> None:
        self.path = Path(path)
        self.strict_packet_lengths = strict_packet_lengths

    @property
    def metadata(self) -> dict[str, object]:
        with self.path.open("rb") as file:
            _, metadata_length = CaptureWriter._validate_header(file)
            file.seek(_HEADER.size)
            document = file.read(metadata_length)
        return _parse_metadata(document)

    def __iter__(self) -> Iterator[CaptureRecord]:
        with self.path.open("rb") as file:
            last_timestamp_ns: int | None = None
            header_size, _ = CaptureWriter._validate_header(file)
            file.seek(header_size)
            while True:
                prefix = file.read(_RECORD_PREFIX.size)
                if not prefix:
                    return
                if len(prefix) != _RECORD_PREFIX.size:
                    raise CaptureError("capture record prefix is truncated")
                marker = prefix[:4]
                if marker == _RECORD_MAGIC:
                    _, payload_length, timestamp, port = _RECORD_PREFIX.unpack(prefix)
                    if not math.isfinite(timestamp):
                        raise CaptureError("capture timestamp is not finite")
                    timestamp_ns = round(timestamp * 1_000_000_000)
                    exact_timestamp_ns: int | None = None
                elif marker == _RECORD_NS_MAGIC:
                    _, payload_length, timestamp_ns, port = _RECORD_NS_PREFIX.unpack(
                        prefix
                    )
                    timestamp = timestamp_ns / 1_000_000_000
                    exact_timestamp_ns = timestamp_ns
                else:
                    raise CaptureError("capture record marker is invalid")
                if last_timestamp_ns is not None and timestamp_ns < last_timestamp_ns:
                    raise CaptureError("capture timestamps decrease")
                source_length_raw = file.read(_U32.size)
                if len(source_length_raw) != _U32.size:
                    raise CaptureError("capture source length is truncated")
                source_length = _U32.unpack(source_length_raw)[0]
                if source_length > _MAX_SOURCE_BYTES:
                    raise CaptureError("capture source address is too long")
                if not 1 <= payload_length <= _MAX_UDP_DATAGRAM_SIZE:
                    raise CaptureError("capture datagram exceeds the UDP payload limit")
                if (
                    self.strict_packet_lengths
                    and payload_length not in _FH4_PACKET_LENGTHS
                ):
                    raise CaptureError(
                        "capture datagram length must be 323 or 324 bytes"
                    )
                source = file.read(source_length)
                payload = file.read(payload_length)
                checksum = file.read(_U32.size)
                if (
                    len(source) != source_length
                    or len(payload) != payload_length
                    or len(checksum) != _U32.size
                ):
                    raise CaptureError("capture record is truncated")
                body = prefix + source_length_raw + source + payload
                if zlib.crc32(body) & 0xFFFFFFFF != _U32.unpack(checksum)[0]:
                    raise CaptureError("capture record checksum mismatch")
                try:
                    host = source.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CaptureError("capture source is not UTF-8") from exc
                last_timestamp_ns = timestamp_ns
                yield CaptureRecord(
                    payload,
                    timestamp,
                    (host, port),
                    exact_timestamp_ns,
                )

    def records(self) -> Iterator[CaptureRecord]:
        return iter(self)

    def read_all(self) -> list[CaptureRecord]:
        return list(self)


def replay_capture(
    path: str | Path, *, strict_packet_lengths: bool = True
) -> Iterator[CaptureRecord]:
    """Yield records in file order without touching the network or clock."""
    yield from CaptureReader(path, strict_packet_lengths=strict_packet_lengths)


def recover_capture_tail(path: str | Path) -> int:
    """Truncate an incomplete final datagram and return valid record count."""
    file_path = Path(path)
    with file_path.open("r+b") as file:
        start, _ = CaptureWriter._validate_header(file)
        file.seek(start)
        valid_end = start
        count = 0
        while True:
            record_start = file.tell()
            prefix = file.read(_RECORD_PREFIX.size)
            if not prefix:
                return count
            if len(prefix) != _RECORD_PREFIX.size:
                file.truncate(valid_end)
                return count
            marker = prefix[:4]
            if marker == _RECORD_MAGIC:
                _, length, timestamp, _port = _RECORD_PREFIX.unpack(prefix)
                valid_timestamp = math.isfinite(timestamp)
            elif marker == _RECORD_NS_MAGIC:
                _, length, _timestamp_ns, _port = _RECORD_NS_PREFIX.unpack(prefix)
                valid_timestamp = True
            else:
                length = 0
                valid_timestamp = False
            source_length_raw = file.read(_U32.size)
            if (
                not valid_timestamp
                or not 1 <= length <= _MAX_UDP_DATAGRAM_SIZE
                or len(source_length_raw) != _U32.size
            ):
                file.truncate(valid_end)
                return count
            source_length = _U32.unpack(source_length_raw)[0]
            if source_length > _MAX_SOURCE_BYTES:
                file.truncate(valid_end)
                return count
            source = file.read(source_length)
            payload = file.read(length)
            checksum = file.read(_U32.size)
            body = prefix + source_length_raw + source + payload
            if (
                len(source) != source_length
                or len(payload) != length
                or len(checksum) != _U32.size
                or zlib.crc32(body) & 0xFFFFFFFF != _U32.unpack(checksum)[0]
            ):
                file.truncate(valid_end)
                return count
            valid_end = file.tell()
            count += 1
            if file.tell() == record_start:
                file.truncate(valid_end)
                return count


def inspect_capture(
    path: str | Path, *, strict_packet_lengths: bool = True
) -> dict[str, int | str]:
    """Validate a capture and return deterministic summary counters."""
    count = 0
    payload_bytes = 0
    lengths: set[int] = set()
    for record in CaptureReader(path, strict_packet_lengths=strict_packet_lengths):
        count += 1
        payload_bytes += len(record.payload)
        lengths.add(len(record.payload))
    return {
        "path": str(path),
        "records": count,
        "payload_bytes": payload_bytes,
        "packet_lengths": ",".join(str(length) for length in sorted(lengths)),
    }


PacketCaptureWriter = CaptureWriter
PacketCaptureReader = CaptureReader
CaptureRecordError = CaptureError

__all__ = [
    "CaptureError",
    "CaptureLimitReached",
    "CaptureReader",
    "CaptureRecord",
    "CaptureWriter",
    "PacketCaptureReader",
    "PacketCaptureWriter",
    "inspect_capture",
    "recover_capture_tail",
    "replay_capture",
]
