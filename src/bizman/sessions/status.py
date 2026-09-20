from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceSessionStatus:
    """Schema-validated collector session metadata without event replay."""

    session_id: str
    status: str
    started_at: str
    ended_at: str | None


__all__ = ["EvidenceSessionStatus"]
