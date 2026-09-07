from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MatchState(StrEnum):
    """Result of comparing a runtime observation with curated knowledge."""

    KNOWN = "known"
    NOVEL = "novel"
    INDETERMINATE = "indeterminate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class PathMatch:
    """Deterministic result of matching one origin-relative pathname."""

    matched: bool
    path_pattern: str | None = None
    exact: bool = False


__all__ = ["MatchState", "PathMatch"]
