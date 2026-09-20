from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bizman.changes.baseline import BaselineConsistencyError, BaselineFormatError
from bizman.changes.diff import SemanticContractError
from bizman.changes.promotion import PromotionIntegrityError, PromotionSchemaError
from bizman.changes.rules import RuleApplicationError, RuleConfigurationError
from bizman.changes.runner import DetectorRunSummary as _DetectorRunSummary
from bizman.changes.runner import DetectorRunner
from bizman.changes.state import StateCompatibilityError, StateError, StateIntegrityError
from bizman.core.assets import AssetId
from bizman.core.context import CoreContext
from bizman.core.errors import (
    AssetError,
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.foundation.redaction import load_redaction_policy
from bizman.sessions.evidence import EvidenceError


@dataclass(frozen=True, slots=True)
class DetectorRunSummary:
    analysis_profile_sha256: str
    baseline_sha256: str
    dry_run: bool
    sessions_discovered: int
    sessions_processed: int
    sessions_checkpointed: int
    sessions_failed_skipped: int
    sessions_unfinalized_skipped: int
    evidence_sha256s: tuple[str, ...]
    fact_counts: tuple[tuple[str, int], ...]
    first_seen_change_ids: tuple[str, ...]
    repeated_change_ids: tuple[str, ...]
    materialized_bundle_count: int
    pending_bundle_count: int

    @classmethod
    def from_internal(cls, value: _DetectorRunSummary) -> "DetectorRunSummary":
        if not isinstance(value, _DetectorRunSummary):
            raise TypeError("value must be detector run summary")
        return cls(
            analysis_profile_sha256=value.analysis_profile_sha256,
            baseline_sha256=value.baseline_sha256,
            dry_run=value.dry_run,
            sessions_discovered=value.sessions_discovered,
            sessions_processed=value.sessions_processed,
            sessions_checkpointed=value.sessions_checkpointed,
            sessions_failed_skipped=value.sessions_failed_skipped,
            sessions_unfinalized_skipped=value.sessions_unfinalized_skipped,
            evidence_sha256s=tuple(value.evidence_sha256s),
            fact_counts=tuple(sorted(value.fact_counts.items())),
            first_seen_change_ids=tuple(value.first_seen_change_ids),
            repeated_change_ids=tuple(value.repeated_change_ids),
            materialized_bundle_count=value.materialized_bundle_count,
            pending_bundle_count=value.pending_bundle_count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_profile_sha256": self.analysis_profile_sha256,
            "baseline_sha256": self.baseline_sha256,
            "dry_run": self.dry_run,
            "sessions_discovered": self.sessions_discovered,
            "sessions_processed": self.sessions_processed,
            "sessions_checkpointed": self.sessions_checkpointed,
            "sessions_failed_skipped": self.sessions_failed_skipped,
            "sessions_unfinalized_skipped": self.sessions_unfinalized_skipped,
            "evidence_sha256s": list(self.evidence_sha256s),
            "fact_counts": dict(self.fact_counts),
            "first_seen_change_ids": list(self.first_seen_change_ids),
            "repeated_change_ids": list(self.repeated_change_ids),
            "materialized_bundle_count": self.materialized_bundle_count,
            "pending_bundle_count": self.pending_bundle_count,
        }


@dataclass(frozen=True, slots=True)
class DetectionRequest:
    selected_sessions: tuple[str, ...] = ()
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.selected_sessions, tuple):
            raise TypeError("selected_sessions must be a tuple of strings")
        if not all(isinstance(value, str) and value for value in self.selected_sessions):
            raise TypeError("selected_sessions must contain only non-empty strings")
        if not isinstance(self.dry_run, bool):
            raise TypeError("dry_run must be boolean")


def _clock_string(context: CoreContext) -> str:
    value = context.clock.now_utc()
    if not isinstance(value, datetime):
        raise ConfigurationError("UtcClock.now_utc() must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfigurationError("UtcClock.now_utc() must return timezone-aware UTC")
    normalized = value.astimezone(UTC)
    if value.utcoffset() != normalized.utcoffset():
        raise ConfigurationError("UtcClock.now_utc() must return UTC")
    return normalized.isoformat().replace("+00:00", "Z")


def detect_changes(context: CoreContext, request: DetectionRequest) -> DetectorRunSummary:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, DetectionRequest):
        raise TypeError("request must be DetectionRequest")

    try:
        redaction = load_redaction_policy(context.assets.path(AssetId.REDACTION_POLICY))
    except (OSError, ValueError) as exc:
        raise AssetError("redaction policy is unavailable or invalid") from exc

    try:
        with DetectorRunner.from_paths(
            repo_root=context.assets.root,
            data_dir=context.data_dir,
            redaction=redaction,
            selected=request.selected_sessions,
            dry_run=request.dry_run,
            clock=lambda: _clock_string(context),
        ) as runner:
            return DetectorRunSummary.from_internal(runner.run())
    except BaselineFormatError as exc:
        raise AssetError("curated baseline assets are invalid") from exc
    except (
        BaselineConsistencyError,
        SemanticContractError,
        StateCompatibilityError,
        PromotionSchemaError,
    ) as exc:
        raise ContractMismatchError("detector contracts are incompatible") from exc
    except (EvidenceError, StateIntegrityError, PromotionIntegrityError) as exc:
        raise DataIntegrityError("detector evidence or state failed integrity checks") from exc
    except (RuleConfigurationError, RuleApplicationError) as exc:
        raise ContractMismatchError("detector rule contract is inconsistent") from exc
    except StateError as exc:
        raise OperationError("detector state operation failed") from exc


__all__ = ["DetectionRequest", "DetectorRunSummary", "detect_changes"]