from __future__ import annotations

from dataclasses import dataclass

from bizman.changes.baseline import (
    BaselineConsistencyError,
    BaselineFormatError,
)
from bizman.core.assets import AssetId
from bizman.core.context import CoreContext
from bizman.core.errors import (
    AssetError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.current import (
    CatalogResolverError,
    CurrentStateCompatibilityError,
    CurrentStateIntegrityError,
    CurrentStateOperationError,
    CurrentStateSnapshot,
)
from bizman.current import rebuild_current_state as _rebuild_current_state
from bizman.foundation.redaction import load_redaction_policy
from bizman.sessions.evidence import EvidenceError


@dataclass(frozen=True, slots=True)
class CurrentStateRebuildRequest:
    """Rebuild the fixed Current State projection from immutable evidence."""


@dataclass(frozen=True, slots=True)
class CurrentStateRebuildResult:
    projection_name: str
    projection_version: int
    analysis_profile_sha256: str
    input_fingerprint: str
    state_fingerprint: str
    status: str
    stale_reason: str | None
    session_count: int
    last_session_id: str | None
    last_sequence: int | None

    @classmethod
    def from_snapshot(
        cls,
        value: CurrentStateSnapshot,
    ) -> "CurrentStateRebuildResult":
        if not isinstance(value, CurrentStateSnapshot):
            raise TypeError("value must be CurrentStateSnapshot")
        metadata = value.metadata
        return cls(
            projection_name=metadata.projection_name,
            projection_version=metadata.projection_version,
            analysis_profile_sha256=metadata.analysis_profile_sha256,
            input_fingerprint=metadata.input_fingerprint,
            state_fingerprint=metadata.state_fingerprint,
            status=metadata.status,
            stale_reason=metadata.stale_reason,
            session_count=metadata.session_count,
            last_session_id=metadata.last_session_id,
            last_sequence=metadata.last_sequence,
        )


def rebuild_current_state(
    context: CoreContext,
    request: CurrentStateRebuildRequest,
) -> CurrentStateRebuildResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, CurrentStateRebuildRequest):
        raise TypeError("request must be CurrentStateRebuildRequest")

    try:
        redaction = load_redaction_policy(
            context.assets.path(AssetId.REDACTION_POLICY)
        )
    except (OSError, ValueError) as exc:
        raise AssetError("redaction policy is unavailable or invalid") from exc

    try:
        snapshot = _rebuild_current_state(
            context.assets.root,
            context.data_dir,
            redaction,
        )
    except BaselineFormatError as exc:
        raise AssetError("curated baseline assets are invalid") from exc
    except CatalogResolverError as exc:
        raise AssetError("curated product catalog is unavailable or invalid") from exc
    except BaselineConsistencyError as exc:
        raise ContractMismatchError(
            "Current State analysis profile contracts are incompatible"
        ) from exc
    except CurrentStateCompatibilityError as exc:
        raise ContractMismatchError(
            "Current State database contract is incompatible"
        ) from exc
    except (EvidenceError, CurrentStateIntegrityError) as exc:
        raise DataIntegrityError(
            "Current State evidence or persisted state failed integrity checks"
        ) from exc
    except CurrentStateOperationError as exc:
        raise OperationError("Current State storage operation failed") from exc

    return CurrentStateRebuildResult.from_snapshot(snapshot)


__all__ = [
    "CurrentStateRebuildRequest",
    "CurrentStateRebuildResult",
    "rebuild_current_state",
]
