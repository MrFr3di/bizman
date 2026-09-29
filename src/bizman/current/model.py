from __future__ import annotations

from dataclasses import dataclass
import re

from bizman.foundation.fingerprint import canonical_sha256


PROJECTION_NAME = "bizman.current"
PROJECTION_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_STATUSES = frozenset({"ready", "stale"})


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_uuid7(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _UUID7_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical UUIDv7")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _non_negative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class CurrentProjectionSpec:
    analysis_profile_sha256: str
    projection_name: str = PROJECTION_NAME
    projection_version: int = PROJECTION_VERSION

    def __post_init__(self) -> None:
        _require_text(self.projection_name, name="projection_name")
        if (
            isinstance(self.projection_version, bool)
            or not isinstance(self.projection_version, int)
            or self.projection_version <= 0
        ):
            raise ValueError("projection_version must be a positive integer")
        _require_sha256(
            self.analysis_profile_sha256,
            name="analysis_profile_sha256",
        )


@dataclass(frozen=True, slots=True, order=True)
class ReplaySession:
    started_at: str
    session_id: str
    manifest_sha256: str
    evidence_sha256: str
    ended_at: str
    status: str
    event_count: int
    last_sequence: int | None

    def __post_init__(self) -> None:
        _require_text(self.started_at, name="started_at")
        _require_uuid7(self.session_id, name="session_id")
        _require_sha256(self.manifest_sha256, name="manifest_sha256")
        _require_sha256(self.evidence_sha256, name="evidence_sha256")
        _require_text(self.ended_at, name="ended_at")
        if self.status not in {"completed", "cancelled"}:
            raise ValueError("replay session status must be completed or cancelled")
        event_count = _non_negative_int(self.event_count, name="event_count")
        if event_count == 0:
            if self.last_sequence is not None:
                raise ValueError("zero-event session requires last_sequence=None")
        else:
            if (
                isinstance(self.last_sequence, bool)
                or not isinstance(self.last_sequence, int)
                or self.last_sequence != event_count - 1
            ):
                raise ValueError(
                    "last_sequence must equal event_count - 1 for non-empty sessions"
                )


@dataclass(frozen=True, slots=True)
class CurrentStateMetadata:
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

    def __post_init__(self) -> None:
        _require_text(self.projection_name, name="projection_name")
        if (
            isinstance(self.projection_version, bool)
            or not isinstance(self.projection_version, int)
            or self.projection_version <= 0
        ):
            raise ValueError("projection_version must be a positive integer")
        _require_sha256(
            self.analysis_profile_sha256,
            name="analysis_profile_sha256",
        )
        _require_sha256(self.input_fingerprint, name="input_fingerprint")
        _require_sha256(self.state_fingerprint, name="state_fingerprint")
        if self.status not in _STATUSES:
            raise ValueError("status must be ready or stale")
        if self.status == "ready" and self.stale_reason is not None:
            raise ValueError("ready Current State cannot carry stale_reason")
        if self.status == "stale":
            _require_text(self.stale_reason, name="stale_reason")
        session_count = _non_negative_int(self.session_count, name="session_count")
        if session_count == 0:
            if self.last_session_id is not None or self.last_sequence is not None:
                raise ValueError(
                    "empty Current State cannot carry last session checkpoint"
                )
        else:
            _require_uuid7(self.last_session_id, name="last_session_id")
            if self.last_sequence is not None:
                _non_negative_int(self.last_sequence, name="last_sequence")


@dataclass(frozen=True, slots=True)
class CurrentStateSnapshot:
    metadata: CurrentStateMetadata
    sessions: tuple[ReplaySession, ...]

    def __post_init__(self) -> None:
        sessions = tuple(self.sessions)
        if len({item.session_id for item in sessions}) != len(sessions):
            raise ValueError("Current State replay ledger contains duplicate sessions")
        ordered = tuple(sorted(sessions))
        if sessions != ordered:
            raise ValueError(
                "Current State replay ledger must be ordered by started_at/session_id"
            )
        if self.metadata.session_count != len(sessions):
            raise ValueError("Current State session_count disagrees with replay ledger")

        if sessions:
            last = sessions[-1]
            if self.metadata.last_session_id != last.session_id:
                raise ValueError("Current State last_session_id disagrees with ledger")
            if self.metadata.last_sequence != last.last_sequence:
                raise ValueError("Current State last_sequence disagrees with ledger")
        elif (
            self.metadata.last_session_id is not None
            or self.metadata.last_sequence is not None
        ):
            raise ValueError("empty Current State cannot carry checkpoint identity")

        expected_input = current_input_fingerprint(
            CurrentProjectionSpec(
                projection_name=self.metadata.projection_name,
                projection_version=self.metadata.projection_version,
                analysis_profile_sha256=self.metadata.analysis_profile_sha256,
            ),
            sessions,
        )
        if self.metadata.input_fingerprint != expected_input:
            raise ValueError("Current State input_fingerprint is inconsistent")

        expected_state = current_state_fingerprint(
            projection_name=self.metadata.projection_name,
            projection_version=self.metadata.projection_version,
            analysis_profile_sha256=self.metadata.analysis_profile_sha256,
            input_fingerprint=self.metadata.input_fingerprint,
            status=self.metadata.status,
            stale_reason=self.metadata.stale_reason,
            sessions=sessions,
        )
        if self.metadata.state_fingerprint != expected_state:
            raise ValueError("Current State state_fingerprint is inconsistent")
        object.__setattr__(self, "sessions", sessions)


def _session_semantics(value: ReplaySession) -> dict[str, object]:
    return {
        "session_id": value.session_id,
        "manifest_sha256": value.manifest_sha256,
        "evidence_sha256": value.evidence_sha256,
        "started_at": value.started_at,
        "ended_at": value.ended_at,
        "status": value.status,
        "event_count": value.event_count,
        "last_sequence": value.last_sequence,
    }


def current_input_fingerprint(
    spec: CurrentProjectionSpec,
    sessions: tuple[ReplaySession, ...],
) -> str:
    if not isinstance(spec, CurrentProjectionSpec):
        raise TypeError("spec must be CurrentProjectionSpec")
    values = tuple(sessions)
    if values != tuple(sorted(values)):
        raise ValueError("sessions must be ordered by started_at/session_id")
    if len({item.session_id for item in values}) != len(values):
        raise ValueError("sessions contain duplicate session_id values")
    return canonical_sha256(
        {
            "projection_name": spec.projection_name,
            "projection_version": spec.projection_version,
            "analysis_profile_sha256": spec.analysis_profile_sha256,
            "sessions": [_session_semantics(item) for item in values],
        }
    )


def current_state_fingerprint(
    *,
    projection_name: str,
    projection_version: int,
    analysis_profile_sha256: str,
    input_fingerprint: str,
    status: str,
    stale_reason: str | None,
    sessions: tuple[ReplaySession, ...],
) -> str:
    return canonical_sha256(
        {
            "projection_name": projection_name,
            "projection_version": projection_version,
            "analysis_profile_sha256": analysis_profile_sha256,
            "input_fingerprint": input_fingerprint,
            "status": status,
            "stale_reason": stale_reason,
            "sessions": [_session_semantics(item) for item in sessions],
        }
    )


def build_current_snapshot(
    spec: CurrentProjectionSpec,
    sessions: tuple[ReplaySession, ...],
    *,
    status: str = "ready",
    stale_reason: str | None = None,
) -> CurrentStateSnapshot:
    values = tuple(sessions)
    if values != tuple(sorted(values)):
        raise ValueError("sessions must be ordered by started_at/session_id")
    input_fingerprint = current_input_fingerprint(spec, values)
    state_fingerprint = current_state_fingerprint(
        projection_name=spec.projection_name,
        projection_version=spec.projection_version,
        analysis_profile_sha256=spec.analysis_profile_sha256,
        input_fingerprint=input_fingerprint,
        status=status,
        stale_reason=stale_reason,
        sessions=values,
    )
    last = values[-1] if values else None
    metadata = CurrentStateMetadata(
        projection_name=spec.projection_name,
        projection_version=spec.projection_version,
        analysis_profile_sha256=spec.analysis_profile_sha256,
        input_fingerprint=input_fingerprint,
        state_fingerprint=state_fingerprint,
        status=status,
        stale_reason=stale_reason,
        session_count=len(values),
        last_session_id=last.session_id if last is not None else None,
        last_sequence=last.last_sequence if last is not None else None,
    )
    return CurrentStateSnapshot(metadata=metadata, sessions=values)


__all__ = [
    "PROJECTION_NAME",
    "PROJECTION_VERSION",
    "CurrentProjectionSpec",
    "CurrentStateMetadata",
    "CurrentStateSnapshot",
    "ReplaySession",
    "build_current_snapshot",
    "current_input_fingerprint",
    "current_state_fingerprint",
]
