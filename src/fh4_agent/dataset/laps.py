"""Conservative race/lap segmentation for candidate human baselines."""

from __future__ import annotations

from ..telemetry import FH4TelemetryPacket
from .contracts import LapCandidate


class LapStateMachine:
    """Emit only laps observed from one boundary through the next.

    Duplicate game timestamps and uint32 wrap are valid. Any race exit or true
    game/race/lap clock regression discards the in-progress lap. Collision
    status remains unknown because FH4 Data Out does not provide sufficient
    collision evidence.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._active = False
        self._boundary_seen = False
        self._lap = 0
        self._lap_start_ns = 0
        self._lap_start_race_s = 0.0
        self._game_ms = 0
        self._race_s = 0.0

    def _start_partial(self, session_ns: int, packet: FH4TelemetryPacket) -> None:
        self._active = True
        self._boundary_seen = False
        self._lap = packet.lap_number
        self._lap_start_ns = session_ns
        self._lap_start_race_s = packet.current_race_time
        self._game_ms = packet.timestamp_ms
        self._race_s = packet.current_race_time

    def reset(self) -> None:
        self._active = False
        self._boundary_seen = False

    def _game_clock_regressed(self, current_ms: int) -> bool:
        delta = (current_ms - self._game_ms) & 0xFFFFFFFF
        return delta > 0x7FFFFFFF

    def update(
        self, session_ns: int, packet: FH4TelemetryPacket
    ) -> LapCandidate | None:
        if not packet.is_race_on:
            self.reset()
            return None
        if not self._active:
            self._start_partial(session_ns, packet)
            return None
        if (
            self._game_clock_regressed(packet.timestamp_ms)
            or packet.current_race_time < self._race_s
            or packet.lap_number < self._lap
        ):
            self._start_partial(session_ns, packet)
            return None

        candidate: LapCandidate | None = None
        if packet.lap_number != self._lap:
            if packet.lap_number == self._lap + 1:
                if self._boundary_seen:
                    duration = packet.current_race_time - self._lap_start_race_s
                    if duration > 0:
                        candidate = LapCandidate(
                            self.session_id,
                            self._lap,
                            self._lap_start_ns,
                            session_ns,
                            duration,
                        )
                self._boundary_seen = True
            else:
                self._boundary_seen = False
            self._lap = packet.lap_number
            self._lap_start_ns = session_ns
            self._lap_start_race_s = packet.current_race_time
        self._game_ms = packet.timestamp_ms
        self._race_s = packet.current_race_time
        return candidate


__all__ = ["LapStateMachine"]
