"""Core use case: deterministic rebuild of the derived Agent Index.

The Agent Index lives at the fixed derived path
``<BizManData>/index/agent-index.sqlite3`` and is a rebuildable projection of
curated repository knowledge plus finalized sanitized runtime evidence and
detector change summaries. Callers never provide a database path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bizman.changes.query import ChangeSummaryReader
from bizman.core.context import CoreContext
from bizman.core.errors import (
    AssetError,
    DataIntegrityError,
    OperationError,
)
from bizman.readmodel import (
    project_curated_knowledge,
    project_runtime_intelligence,
)
from bizman.readmodel.store import (
    KnowledgeIndex,
    ReadModelCompatibilityError,
    ReadModelError,
    ReadModelIntegrityError,
    rebuild_agent_index as _rebuild_agent_index,
)
from bizman.sessions.evidence import EvidenceError, EvidenceReader

_DETECTOR_STATE_SUBPATH = ("detector", "state.sqlite3")
_INDEX_SUBPATH = ("index", "agent-index.sqlite3")


@dataclass(frozen=True, slots=True)
class AgentIndexRebuildRequest:
    """Rebuild the fixed Agent Index projection from immutable inputs."""


@dataclass(frozen=True, slots=True)
class AgentIndexRebuildResult:
    generation: str
    schema_version: str
    projection_version: str
    item_count: int
    session_count: int
    change_count: int
    completed_at: str


def _clock_string(context: CoreContext) -> str:
    value = context.clock.now_utc()
    if not isinstance(value, datetime):
        raise OperationError("UtcClock.now_utc() must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise OperationError("UtcClock.now_utc() must return timezone-aware UTC")
    normalized = value.astimezone(UTC)
    if value.utcoffset() != normalized.utcoffset():
        raise OperationError("UtcClock.now_utc() must return UTC")
    return normalized.isoformat().replace("+00:00", "Z")


def rebuild_agent_index(
    context: CoreContext,
    request: AgentIndexRebuildRequest,
) -> AgentIndexRebuildResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, AgentIndexRebuildRequest):
        raise TypeError("request must be AgentIndexRebuildRequest")

    completed_at = _clock_string(context)
    try:
        projection = project_curated_knowledge(context.assets.root)
    except (OSError, ValueError) as exc:
        raise AssetError("curated knowledge projection is unavailable or invalid") from exc

    reader = EvidenceReader(context.assets.root, context.data_dir)
    change_reader: ChangeSummaryReader | None = None
    try:
        change_reader = ChangeSummaryReader.open_if_exists(
            context.data_dir.joinpath(*_DETECTOR_STATE_SUBPATH),
        )
        runtime = project_runtime_intelligence(reader, change_reader)
    except EvidenceError as exc:
        raise DataIntegrityError("finalized evidence failed verification") from exc
    finally:
        if change_reader is not None:
            change_reader.close()

    target = context.data_dir.joinpath(*_INDEX_SUBPATH)
    try:
        generation = _rebuild_agent_index(
            target,
            projection,
            runtime,
            completed_at=completed_at,
        )
    except (ReadModelCompatibilityError, ReadModelError, ReadModelIntegrityError) as exc:
        raise OperationError("Agent Index rebuild operation failed") from exc
    except OSError as exc:
        raise OperationError("Agent Index rebuild operation failed") from exc

    try:
        with KnowledgeIndex(target) as index:
            metadata = index.metadata()
    except (
        OSError,
        ReadModelError,
        ReadModelCompatibilityError,
        ReadModelIntegrityError,
    ) as exc:
        raise OperationError("Agent Index rebuild verification failed") from exc

    return AgentIndexRebuildResult(
        generation=generation,
        schema_version=metadata["schema_version"],
        projection_version=metadata["projection_version"],
        item_count=int(metadata["item_count"]),
        session_count=int(metadata["session_count"]),
        change_count=int(metadata["change_count"]),
        completed_at=completed_at,
    )


__all__ = ["AgentIndexRebuildRequest", "AgentIndexRebuildResult", "rebuild_agent_index"]
