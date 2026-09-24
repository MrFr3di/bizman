from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from bizman.changes.model import ChangeSummary

if TYPE_CHECKING:
    from bizman.changes.state import DetectorState


class ChangeSummaryReader:
    """Narrow read-only detector-state boundary for downstream projections."""

    def __init__(self, state: DetectorState) -> None:
        self._state = state
        self._closed = False

    @classmethod
    def open_if_exists(cls, path: Path) -> ChangeSummaryReader | None:
        from bizman.changes.state import DetectorState

        state = DetectorState.open_read_only_if_exists(path)
        if state is None:
            return None
        return cls(state)

    def close(self) -> None:
        if self._closed:
            return
        self._state.close()
        self._closed = True

    def __enter__(self) -> ChangeSummaryReader:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def iter_summaries(self) -> Iterator[ChangeSummary]:
        if self._closed:
            raise RuntimeError("change summary reader is closed")
        yield from self._state.iter_change_summaries()


__all__ = ["ChangeSummaryReader"]
