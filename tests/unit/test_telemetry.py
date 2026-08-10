from __future__ import annotations

import errno
import math
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from fh4_agent.capture import CaptureError, CaptureReader, CaptureRecord, CaptureWriter
from fh4_agent.telemetry import (
    PACKET_SIZE,
    TelemetryDecodeError,
    TimestampContinuity,
    decode_packet,
)


def golden_packet(*, trailing: bool = False) -> bytes:
    raw = bytearray(PACKET_SIZE + int(trailing))
    struct.pack_into("<iI", raw, 0, 1, 0xFFFFFFFF)
    struct.pack_into("<f", raw, 8, 9000.0)
    struct.pack_into("<f", raw, 256, 42.5)
    raw[312:314] = (3).to_bytes(2, "little")
    raw[314:323] = bytes((4, 128, 64, 32, 255, 7, 127, -10 & 255, 20))
    if trailing:
        raw[-1] = 0xA5
    return bytes(raw)


@pytest.mark.parametrize("trailing", [False, True])
def test_strict_decoder_and_normalized_adapter(trailing: bool) -> None:
    packet = decode_packet(golden_packet(trailing=trailing))
    assert packet.timestamp_ms == 0xFFFFFFFF
    assert packet.engine_max_rpm == pytest.approx(9000.0)
    assert packet.speed == pytest.approx(42.5)
    assert packet.lap_number == 3
    assert packet.race_position == 4
    assert packet.throttle_normalized == pytest.approx(128 / 255)
    assert packet.steering_normalized == pytest.approx(127 / 127)
    assert packet.horizon_extra == b"\0" * 12
    assert packet.optional_trailing_byte == (b"\xa5" if trailing else b"")
    sample = packet.to_telemetry_sample(12.0)
    assert sample.timestamp == 12.0
    assert sample.speed_mps == pytest.approx(42.5)


def test_decoder_rejects_size_race_flag_and_nonfinite() -> None:
    with pytest.raises(TelemetryDecodeError, match="length"):
        decode_packet(b"\0" * 322)
    invalid_flag = bytearray(golden_packet())
    struct.pack_into("<i", invalid_flag, 0, 2)
    with pytest.raises(TelemetryDecodeError, match="IsRaceOn"):
        decode_packet(invalid_flag)
    nonfinite = bytearray(golden_packet())
    struct.pack_into("<f", nonfinite, 8, float("nan"))
    with pytest.raises(TelemetryDecodeError, match="non-finite"):
        decode_packet(nonfinite)


def test_timestamp_wrap_duplicate_order_and_gap() -> None:
    tracker = TimestampContinuity(expected_interval_ms=10, gap_tolerance_ms=0)
    assert tracker.update(0xFFFFFFF0).kind == "first"
    assert tracker.update(0xFFFFFFFA).kind == "forward"
    assert tracker.update(0xFFFFFFFA).is_duplicate
    wrapped = tracker.update(4)
    assert wrapped.is_wrap and wrapped.delta_ms == 10
    gap = tracker.update(44)
    assert gap.estimated_missing_packets == 3
    stale = tracker.update(0xFFFFFFFA)
    assert stale.is_out_of_order and stale.delta_ms is not None and stale.delta_ms < 0
    assert tracker.update(20).is_out_of_order
    assert tracker.diagnostics()["estimated_missing_packets"] == 3


def test_capture_replay_is_exact_and_detects_truncation(tmp_path: Path) -> None:
    path = tmp_path / "capture.fh4cap"
    records = [
        CaptureRecord(golden_packet(), 10.25, ("127.0.0.1", 5300)),
        CaptureRecord(golden_packet(trailing=True), 10.5, ("192.0.2.5", 5300)),
    ]
    with CaptureWriter(path, metadata={"fixture": "golden"}) as writer:
        for record in records:
            writer.append(record)
    assert list(CaptureReader(path)) == records
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(CaptureError, match="truncated"):
        list(CaptureReader(path))


def test_receiver_fake_socket_lifecycle_and_timeout() -> None:
    from fh4_agent.telemetry.receiver import UdpTelemetryReceiver

    class FakeSocket:
        def __init__(self) -> None:
            self.bound: tuple[str, int] | None = None
            self.timeouts: list[float | None] = []
            self.closed = False

        def bind(self, address: tuple[str, int]) -> None:
            self.bound = address

        def settimeout(self, timeout: float | None) -> None:
            self.timeouts.append(timeout)

        def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
            return golden_packet(), ("203.0.113.4", 5300)

        def close(self) -> None:
            self.closed = True

    fake = FakeSocket()
    receiver = UdpTelemetryReceiver(
        "127.0.0.1", 5301, socket_factory=lambda *_: fake, monotonic=lambda: 8.5
    )
    first = receiver.receive_once(0.25)
    second = receiver.receive_once(None)
    assert first.source == ("203.0.113.4", 5300)
    assert first.timestamp_monotonic_s == 8.5
    assert second.payload == golden_packet()
    assert fake.timeouts == [0.25, None]
    receiver.close()
    assert fake.closed


def test_receiver_invalid_timeout_does_not_create_socket() -> None:
    from fh4_agent.telemetry import UdpTelemetryReceiver

    created = 0

    def factory(*_: object) -> Any:
        nonlocal created
        created += 1
        raise AssertionError("socket must not be created")

    receiver = UdpTelemetryReceiver(socket_factory=factory)
    with pytest.raises(ValueError, match="timeout_s"):
        receiver.receive_once(-0.1)
    assert created == 0


def test_receiver_public_error_exports() -> None:
    from fh4_agent.telemetry import OversizeDatagramError, ReceiverError

    assert issubclass(OversizeDatagramError, ReceiverError)


@pytest.mark.parametrize("error_code", [errno.EMSGSIZE, 10040])
def test_receiver_normalizes_os_oversize_errors(error_code: int) -> None:
    from fh4_agent.telemetry import OversizeDatagramError, UdpTelemetryReceiver

    class FakeSocket:
        def __init__(self) -> None:
            self.receive_sizes: list[int] = []

        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
            self.receive_sizes.append(size)
            raise OSError(error_code, "message too long")

        def close(self) -> None:
            pass

    fake = FakeSocket()
    receiver = UdpTelemetryReceiver(socket_factory=lambda *_: fake)
    with pytest.raises(OversizeDatagramError, match="truncated"):
        receiver.receive_once()
    assert fake.receive_sizes == [receiver.max_datagram_size + 1]
    receiver.close()


def test_receiver_preserves_unrelated_socket_errors() -> None:
    from fh4_agent.telemetry import ReceiverError, UdpTelemetryReceiver

    class FakeSocket:
        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
            raise OSError(errno.ECONNRESET, "connection reset")

        def close(self) -> None:
            pass

    receiver = UdpTelemetryReceiver(socket_factory=lambda *_: FakeSocket())
    with pytest.raises(OSError, match="connection reset") as caught:
        receiver.receive_once()
    assert not isinstance(caught.value, ReceiverError)
    receiver.close()


def test_receiver_rejects_oversize_and_closes_bind_failure() -> None:
    from fh4_agent.telemetry.receiver import OversizeDatagramError, UdpTelemetryReceiver

    class FakeSocket:
        def __init__(self, fail_bind: bool = False) -> None:
            self.fail_bind = fail_bind
            self.closed = False

        def bind(self, _address: tuple[str, int]) -> None:
            if self.fail_bind:
                raise OSError("bind failed")

        def settimeout(self, _timeout: float | None) -> None:
            pass

        def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
            return b"x" * 325, ("127.0.0.1", 1)

        def close(self) -> None:
            self.closed = True

    oversized = FakeSocket()
    receiver = UdpTelemetryReceiver(
        max_datagram_size=324, socket_factory=lambda *_: oversized
    )
    with pytest.raises(OversizeDatagramError):
        receiver.receive_once()
    receiver.close()
    failed = FakeSocket(fail_bind=True)
    receiver = UdpTelemetryReceiver(socket_factory=lambda *_: failed)
    with pytest.raises(OSError, match="bind failed"):
        receiver.receive_once()
    assert failed.closed


def test_capture_reader_strict_lengths_and_nonfinite_order(tmp_path: Path) -> None:
    path = tmp_path / "raw.fh4cap"
    with CaptureWriter(path) as writer:
        writer.append(CaptureRecord(b"raw", 1.0, ("127.0.0.1", 1)))
    with pytest.raises(CaptureError, match="length"):
        list(CaptureReader(path))
    assert list(CaptureReader(path, strict_packet_lengths=False))[0].payload == b"raw"
    with CaptureWriter(tmp_path / "finite.fh4cap") as writer:
        with pytest.raises(CaptureError, match="finite"):
            writer.append(CaptureRecord(golden_packet(), math.nan, ("127.0.0.1", 1)))


def test_capture_metadata_crc_valid_but_malformed(tmp_path: Path) -> None:
    from fh4_agent.capture.telemetry import FORMAT_VERSION, MAGIC

    path = tmp_path / "bad-meta.fh4cap"
    document = b"[]"
    path.write_bytes(
        struct.pack("<8sHI", MAGIC, FORMAT_VERSION, len(document))
        + document
        + struct.pack("<I", zlib.crc32(document) & 0xFFFFFFFF)
    )
    with pytest.raises(CaptureError, match="metadata"):
        list(CaptureReader(path))


def test_capture_rejects_declared_lengths_before_reading(tmp_path: Path) -> None:
    from fh4_agent.capture.telemetry import FORMAT_VERSION, MAGIC

    document = b"{}"
    header = (
        struct.pack("<8sHI", MAGIC, FORMAT_VERSION, len(document))
        + document
        + struct.pack("<I", zlib.crc32(document) & 0xFFFFFFFF)
    )

    huge_payload = tmp_path / "huge-payload.fh4cap"
    huge_payload.write_bytes(
        header + struct.pack("<4sIdH", b"DG01", 65508, 1.0, 5300) + struct.pack("<I", 0)
    )
    with pytest.raises(CaptureError, match="UDP payload limit"):
        list(CaptureReader(huge_payload, strict_packet_lengths=False))

    huge_source = tmp_path / "huge-source.fh4cap"
    huge_source.write_bytes(
        header
        + struct.pack("<4sIdH", b"DG01", PACKET_SIZE, 1.0, 5300)
        + struct.pack("<I", 65536)
    )
    with pytest.raises(CaptureError, match="source address"):
        list(CaptureReader(huge_source))


def test_cli_bounded_capture_uses_fake_receiver(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from fh4_agent import cli

    class FakeReceiver:
        def __init__(self, host: str, port: int) -> None:
            self.host, self.port = host, port
            self.calls = 0

        def receive_once(self, timeout: float | None) -> Any:
            self.calls += 1
            return type(
                "Record",
                (),
                {
                    "payload": golden_packet(),
                    "timestamp_monotonic_s": float(self.calls),
                    "source": ("127.0.0.1", 5300),
                },
            )()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "UdpTelemetryReceiver", FakeReceiver)
    output = tmp_path / "cli.fh4cap"
    assert (
        cli.main(["capture", str(output), "--packets", "2", "--timeout-s", "0.1"]) == 0
    )
    assert len(list(CaptureReader(output))) == 2
