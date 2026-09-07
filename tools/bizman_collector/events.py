from __future__ import annotations

from datetime import UTC, datetime
import time


class EventSequencer:
    """Allocate contiguous event sequence numbers for one collector session."""

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        value = self._value
        self._value += 1
        return value


class CollectorClock:
    """Clock domain shared by every event producer in one collector process."""

    def monotonic(self) -> float:
        return time.monotonic()

    def wall_iso(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
