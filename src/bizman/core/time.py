from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class UtcClock(Protocol):
    def now(self) -> datetime: ...


class SystemUtcClock:
    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


__all__ = ["SystemUtcClock", "UtcClock"]
