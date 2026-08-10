"""Lazy UDP acquisition for FH4 datagrams.

Importing this module never creates a socket or binds a port.  Sockets are
created only when :meth:`UdpTelemetryReceiver.receive_once` is called.
"""

from __future__ import annotations

import errno
import math
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReceivedDatagram:
    """Raw datagram with the local monotonic receive time and source."""

    payload: bytes
    timestamp_monotonic_s: float
    source: tuple[str, int]

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("payload must be non-empty")
        if not math.isfinite(self.timestamp_monotonic_s):
            raise ValueError("timestamp_monotonic_s must be finite")
        if not isinstance(self.source, tuple) or len(self.source) != 2:
            raise ValueError("source must be (host, port)")
        if not isinstance(self.source[1], int) or not 0 <= self.source[1] <= 65535:
            raise ValueError("source port must be between 0 and 65535")

    @property
    def monotonic_timestamp(self) -> float:
        return self.timestamp_monotonic_s

    @property
    def source_address(self) -> tuple[str, int]:
        return self.source


class ReceiverError(ValueError):
    """Raised when a UDP datagram cannot be safely acquired."""


class OversizeDatagramError(ReceiverError):
    """Raised when the received datagram exceeds the configured bound."""


SocketFactory = Callable[[int, int], socket.socket]


def _default_socket_factory(family: int, sock_type: int) -> socket.socket:
    return socket.socket(family, sock_type)


class UdpTelemetryReceiver:
    """Receive raw FH4 datagrams without any import-time network activity."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5300,
        *,
        max_datagram_size: int = 1500,
        socket_factory: SocketFactory = _default_socket_factory,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 0 <= port <= 65535
        ):
            raise ValueError("port must be between 0 and 65535")
        if (
            not isinstance(max_datagram_size, int)
            or isinstance(max_datagram_size, bool)
            or not 324 <= max_datagram_size <= 65507
        ):
            raise ValueError("max_datagram_size must be between 324 and 65507")
        self.host = host
        self.port = port
        self.max_datagram_size = max_datagram_size
        self._socket_factory = socket_factory
        self._monotonic = monotonic
        self._socket: socket.socket | None = None

    def _ensure_socket(self) -> socket.socket:
        if self._socket is None:
            sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((self.host, self.port))
            except BaseException:
                sock.close()
                raise
            self._socket = sock
        return self._socket

    @property
    def socket(self) -> socket.socket | None:
        return self._socket

    @staticmethod
    def _validate_timeout(timeout_s: float | None) -> float | None:
        if timeout_s is not None and (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or timeout_s < 0
        ):
            raise ValueError("timeout_s must be a finite non-negative number")
        return None if timeout_s is None else float(timeout_s)

    def receive_once(self, timeout_s: float | None = None) -> ReceivedDatagram:
        timeout = self._validate_timeout(timeout_s)
        sock = self._ensure_socket()
        # Read one byte beyond the configured bound.  This detects truncation
        # on platforms that otherwise silently return a clipped datagram.
        sock.settimeout(timeout)
        try:
            payload, source = sock.recvfrom(self.max_datagram_size + 1)
        except OSError as exc:
            if (
                exc.errno in {errno.EMSGSIZE, 10040}
                or getattr(exc, "winerror", None) == 10040
            ):
                raise OversizeDatagramError(
                    "UDP datagram was truncated by the operating system"
                ) from exc
            raise
        if len(payload) > self.max_datagram_size:
            raise OversizeDatagramError(
                f"UDP datagram exceeds max_datagram_size {self.max_datagram_size}"
            )
        received_at = self._monotonic()
        if not isinstance(source, tuple) or len(source) < 2:
            raise OSError("UDP source address is malformed")
        return ReceivedDatagram(
            bytes(payload), received_at, (str(source[0]), int(source[1]))
        )

    def records(
        self, *, max_packets: int | None = None, timeout_s: float | None = None
    ) -> Iterator[ReceivedDatagram]:
        count = 0
        while max_packets is None or count < max_packets:
            yield self.receive_once(timeout_s)
            count += 1

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> UdpTelemetryReceiver:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# Short aliases for callers that use the acquisition terminology.
TelemetryReceiver = UdpTelemetryReceiver
DatagramRecord = ReceivedDatagram

__all__ = [
    "DatagramRecord",
    "OversizeDatagramError",
    "ReceiverError",
    "ReceivedDatagram",
    "TelemetryReceiver",
    "UdpTelemetryReceiver",
]
