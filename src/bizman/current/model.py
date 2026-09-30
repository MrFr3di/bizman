from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re

from bizman.foundation.fingerprint import canonical_sha256


PROJECTION_NAME = "bizman.current"
PROJECTION_VERSION = 2
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


def _require_entity_id(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise ValueError(f"{name} must be a canonical positive decimal identifier")
    return text


def _canonical_instant(value: object, *, name: str) -> tuple[str, datetime]:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    utc = instant.astimezone(UTC)
    return utc.isoformat().replace("+00:00", "Z"), utc


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
        started_text, started = _canonical_instant(
            self.started_at,
            name="started_at",
        )
        ended_text, ended = _canonical_instant(
            self.ended_at,
            name="ended_at",
        )
        if ended < started:
            raise ValueError("ended_at cannot precede started_at")
        object.__setattr__(self, "started_at", started_text)
        object.__setattr__(self, "ended_at", ended_text)
        _require_uuid7(self.session_id, name="session_id")
        _require_sha256(self.manifest_sha256, name="manifest_sha256")
        _require_sha256(self.evidence_sha256, name="evidence_sha256")
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


@dataclass(frozen=True, slots=True, order=True)
class CompanyState:
    company_id: str
    name: str
    source_session_id: str
    source_sequence: int
    observed_at: str

    def __post_init__(self) -> None:
        _require_entity_id(self.company_id, name="company_id")
        name = _require_text(self.name, name="name").strip()
        if not name:
            raise ValueError("name must contain non-whitespace text")
        object.__setattr__(self, "name", name)
        _require_uuid7(self.source_session_id, name="source_session_id")
        _non_negative_int(self.source_sequence, name="source_sequence")
        observed, _ = _canonical_instant(self.observed_at, name="observed_at")
        object.__setattr__(self, "observed_at", observed)


@dataclass(frozen=True, slots=True, order=True)
class UnitState:
    unit_id: str
    company_id: str
    display_name: str
    city_name: str
    level: int
    source_session_id: str
    source_sequence: int
    observed_at: str

    def __post_init__(self) -> None:
        _require_entity_id(self.unit_id, name="unit_id")
        _require_entity_id(self.company_id, name="company_id")
        display_name = _require_text(self.display_name, name="display_name").strip()
        city_name = _require_text(self.city_name, name="city_name").strip()
        if not display_name or not city_name:
            raise ValueError("unit display_name and city_name must contain text")
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "city_name", city_name)
        if (
            isinstance(self.level, bool)
            or not isinstance(self.level, int)
            or self.level <= 0
        ):
            raise ValueError("level must be a positive integer")
        _require_uuid7(self.source_session_id, name="source_session_id")
        _non_negative_int(self.source_sequence, name="source_sequence")
        observed, _ = _canonical_instant(self.observed_at, name="observed_at")
        object.__setattr__(self, "observed_at", observed)


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


def _validate_snapshot_ledger(
    metadata: CurrentStateMetadata,
    session_values: tuple[ReplaySession, ...],
) -> tuple[ReplaySession, ...]:
    sessions = tuple(session_values)
    if not all(isinstance(item, ReplaySession) for item in sessions):
        raise TypeError("sessions must contain only ReplaySession values")
    if len({item.session_id for item in sessions}) != len(sessions):
        raise ValueError("Current State replay ledger contains duplicate sessions")
    ordered = tuple(sorted(sessions))
    if sessions != ordered:
        raise ValueError(
            "Current State replay ledger must be ordered by started_at/session_id"
        )
    if metadata.session_count != len(sessions):
        raise ValueError("Current State session_count disagrees with replay ledger")

    if sessions:
        last = sessions[-1]
        if metadata.last_session_id != last.session_id:
            raise ValueError("Current State last_session_id disagrees with ledger")
        if metadata.last_sequence != last.last_sequence:
            raise ValueError("Current State last_sequence disagrees with ledger")
    elif (
        metadata.last_session_id is not None
        or metadata.last_sequence is not None
    ):
        raise ValueError("empty Current State cannot carry checkpoint identity")

    return sessions


def _validate_snapshot_domain(
    sessions: tuple[ReplaySession, ...],
    company_values: tuple[CompanyState, ...],
    unit_values: tuple[UnitState, ...],
) -> tuple[tuple[CompanyState, ...], tuple[UnitState, ...]]:
    companies = tuple(company_values)
    units = tuple(unit_values)
    if not all(isinstance(item, CompanyState) for item in companies):
        raise TypeError("companies must contain only CompanyState values")
    if not all(isinstance(item, UnitState) for item in units):
        raise TypeError("units must contain only UnitState values")
    if len({item.company_id for item in companies}) != len(companies):
        raise ValueError("Current State contains duplicate company_id values")
    if len({item.unit_id for item in units}) != len(units):
        raise ValueError("Current State contains duplicate unit_id values")
    if companies != tuple(sorted(companies, key=lambda item: item.company_id)):
        raise ValueError("companies must be ordered by company_id")
    if units != tuple(sorted(units, key=lambda item: item.unit_id)):
        raise ValueError("units must be ordered by unit_id")

    session_by_id = {item.session_id: item for item in sessions}
    company_ids = {item.company_id for item in companies}
    for value in (*companies, *units):
        session = session_by_id.get(value.source_session_id)
        if session is None:
            raise ValueError("domain provenance references an unreplayed session")
        if value.source_sequence >= session.event_count:
            raise ValueError("domain provenance sequence exceeds replayed session")
    for unit in units:
        if unit.company_id not in company_ids:
            raise ValueError("unit references a company absent from Current State")

    return companies, units


def _validate_snapshot_fingerprints(
    metadata: CurrentStateMetadata,
    sessions: tuple[ReplaySession, ...],
    companies: tuple[CompanyState, ...],
    units: tuple[UnitState, ...],
) -> None:
    expected_input = current_input_fingerprint(
        CurrentProjectionSpec(
            projection_name=metadata.projection_name,
            projection_version=metadata.projection_version,
            analysis_profile_sha256=metadata.analysis_profile_sha256,
        ),
        sessions,
    )
    if metadata.input_fingerprint != expected_input:
        raise ValueError("Current State input_fingerprint is inconsistent")

    expected_state = current_state_fingerprint(
        projection_name=metadata.projection_name,
        projection_version=metadata.projection_version,
        analysis_profile_sha256=metadata.analysis_profile_sha256,
        input_fingerprint=metadata.input_fingerprint,
        status=metadata.status,
        stale_reason=metadata.stale_reason,
        sessions=sessions,
        companies=companies,
        units=units,
    )
    if metadata.state_fingerprint != expected_state:
        raise ValueError("Current State state_fingerprint is inconsistent")



@dataclass(frozen=True, slots=True)
class CurrentStateSnapshot:
    metadata: CurrentStateMetadata
    sessions: tuple[ReplaySession, ...]
    companies: tuple[CompanyState, ...] = ()
    units: tuple[UnitState, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, CurrentStateMetadata):
            raise TypeError("metadata must be CurrentStateMetadata")
        sessions = _validate_snapshot_ledger(self.metadata, self.sessions)
        companies, units = _validate_snapshot_domain(
            sessions, self.companies, self.units,
        )
        _validate_snapshot_fingerprints(
            self.metadata, sessions, companies, units,
        )
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "companies", companies)
        object.__setattr__(self, "units", units)


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
    if not all(isinstance(item, ReplaySession) for item in values):
        raise TypeError("sessions must contain only ReplaySession values")
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


def _company_semantics(value: CompanyState) -> dict[str, object]:
    return {
        "company_id": value.company_id,
        "name": value.name,
        "source_session_id": value.source_session_id,
        "source_sequence": value.source_sequence,
        "observed_at": value.observed_at,
    }


def _unit_semantics(value: UnitState) -> dict[str, object]:
    return {
        "unit_id": value.unit_id,
        "company_id": value.company_id,
        "display_name": value.display_name,
        "city_name": value.city_name,
        "level": value.level,
        "source_session_id": value.source_session_id,
        "source_sequence": value.source_sequence,
        "observed_at": value.observed_at,
    }


def current_state_fingerprint(
    *,
    projection_name: str,
    projection_version: int,
    analysis_profile_sha256: str,
    input_fingerprint: str,
    status: str,
    stale_reason: str | None,
    sessions: tuple[ReplaySession, ...],
    companies: tuple[CompanyState, ...] = (),
    units: tuple[UnitState, ...] = (),
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
            "companies": [_company_semantics(item) for item in companies],
            "units": [_unit_semantics(item) for item in units],
        }
    )


def build_current_snapshot(
    spec: CurrentProjectionSpec,
    sessions: tuple[ReplaySession, ...],
    *,
    companies: tuple[CompanyState, ...] = (),
    units: tuple[UnitState, ...] = (),
    status: str = "ready",
    stale_reason: str | None = None,
) -> CurrentStateSnapshot:
    values = tuple(sessions)
    if not all(isinstance(item, ReplaySession) for item in values):
        raise TypeError("sessions must contain only ReplaySession values")
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
        companies=tuple(companies),
        units=tuple(units),
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
    return CurrentStateSnapshot(
        metadata=metadata,
        sessions=values,
        companies=tuple(companies),
        units=tuple(units),
    )


__all__ = [
    "PROJECTION_NAME",
    "PROJECTION_VERSION",
    "CompanyState",
    "CurrentProjectionSpec",
    "CurrentStateMetadata",
    "CurrentStateSnapshot",
    "ReplaySession",
    "UnitState",
    "build_current_snapshot",
    "current_input_fingerprint",
    "current_state_fingerprint",
]
