from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from bizman.changes import ChangeSummary, ChangeSummaryReader
from bizman.foundation.fingerprint import canonical_sha256
from bizman.sessions import EvidenceReader, EvidenceSessionInfo


RUNTIME_PROJECTION_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _require_non_negative(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_positive(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return text


def _parse_instant(value: object, *, name: str) -> datetime:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    manifest_sha256: str
    evidence_sha256: str
    started_at: str
    ended_at: str
    status: str
    event_count: int
    action_count: int
    http_request_count: int
    http_response_count: int
    correlation_strong_count: int
    correlation_probable_count: int
    correlation_temporal_count: int
    correlation_exact_count: int
    uncorrelated_action_count: int
    warning_count: int
    anomaly_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or _SESSION_ID_RE.fullmatch(self.session_id) is None:
            raise ValueError("session_id must be a canonical UUIDv7")
        _require_sha256(self.manifest_sha256, name="manifest_sha256")
        _require_sha256(self.evidence_sha256, name="evidence_sha256")
        started = _parse_instant(self.started_at, name="started_at")
        ended = _parse_instant(self.ended_at, name="ended_at")
        if ended < started:
            raise ValueError("ended_at must not precede started_at")
        if self.status not in {"completed", "cancelled"}:
            raise ValueError("status must be completed or cancelled")
        for name in (
            "event_count",
            "action_count",
            "http_request_count",
            "http_response_count",
            "correlation_strong_count",
            "correlation_probable_count",
            "correlation_temporal_count",
            "correlation_exact_count",
            "uncorrelated_action_count",
            "warning_count",
            "anomaly_count",
        ):
            _require_non_negative(getattr(self, name), name=name)
        if self.uncorrelated_action_count > self.action_count:
            raise ValueError("uncorrelated_action_count cannot exceed action_count")


@dataclass(frozen=True, slots=True)
class ChangeIndexRecord:
    analysis_profile_sha256: str
    change_id: str
    rule_id: str
    rule_version: int
    kind: str
    novelty_class: str
    first_session_id: str
    first_seen_at: str
    last_session_id: str
    last_seen_at: str
    occurrence_count: int

    def __post_init__(self) -> None:
        _require_sha256(self.analysis_profile_sha256, name="analysis_profile_sha256")
        _require_text(self.change_id, name="change_id")
        _require_text(self.rule_id, name="rule_id")
        _require_positive(self.rule_version, name="rule_version")
        _require_text(self.kind, name="kind")
        _require_text(self.novelty_class, name="novelty_class")
        if _SESSION_ID_RE.fullmatch(_require_text(self.first_session_id, name="first_session_id")) is None:
            raise ValueError("first_session_id must be a canonical UUIDv7")
        if _SESSION_ID_RE.fullmatch(_require_text(self.last_session_id, name="last_session_id")) is None:
            raise ValueError("last_session_id must be a canonical UUIDv7")
        first = _parse_instant(self.first_seen_at, name="first_seen_at")
        last = _parse_instant(self.last_seen_at, name="last_seen_at")
        if last < first:
            raise ValueError("last_seen_at must not precede first_seen_at")
        _require_positive(self.occurrence_count, name="occurrence_count")


def _session_value(item: SessionSummary) -> dict[str, object]:
    return {
        field: getattr(item, field)
        for field in SessionSummary.__dataclass_fields__
    }


def _change_value(item: ChangeIndexRecord) -> dict[str, object]:
    return {
        field: getattr(item, field)
        for field in ChangeIndexRecord.__dataclass_fields__
    }


@dataclass(frozen=True, slots=True)
class RuntimeProjection:
    sessions: tuple[SessionSummary, ...] = ()
    changes: tuple[ChangeIndexRecord, ...] = ()

    def __post_init__(self) -> None:
        sessions = tuple(self.sessions)
        changes = tuple(self.changes)
        if not all(isinstance(item, SessionSummary) for item in sessions):
            raise TypeError("sessions must contain SessionSummary values")
        if not all(isinstance(item, ChangeIndexRecord) for item in changes):
            raise TypeError("changes must contain ChangeIndexRecord values")
        sessions = tuple(sorted(sessions, key=lambda item: item.session_id))
        changes = tuple(
            sorted(
                changes,
                key=lambda item: (item.analysis_profile_sha256, item.change_id),
            )
        )
        if len({item.session_id for item in sessions}) != len(sessions):
            raise ValueError("runtime projection contains duplicate session_id values")
        if len({(item.analysis_profile_sha256, item.change_id) for item in changes}) != len(changes):
            raise ValueError("runtime projection contains duplicate profile/change identities")
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "changes", changes)

    @property
    def source_fingerprint(self) -> str:
        return canonical_sha256(
            {
                "runtime_projection_version": RUNTIME_PROJECTION_VERSION,
                "sessions": [_session_value(item) for item in self.sessions],
                "changes": [_change_value(item) for item in self.changes],
            }
        )


def _change_record(summary: ChangeSummary) -> ChangeIndexRecord:
    return ChangeIndexRecord(
        analysis_profile_sha256=summary.analysis_profile_sha256,
        change_id=summary.change_id,
        rule_id=summary.rule_id,
        rule_version=summary.rule_version,
        kind=summary.kind,
        novelty_class=summary.novelty_class,
        first_session_id=summary.first_session_id,
        first_seen_at=summary.first_seen_at,
        last_session_id=summary.last_session_id,
        last_seen_at=summary.last_seen_at,
        occurrence_count=summary.occurrence_count,
    )


def _session_summary(reader: EvidenceReader, info: EvidenceSessionInfo) -> SessionSummary:
    identity = info.identity
    action_ids: set[str] = set()
    correlated_action_ids: set[str] = set()
    event_count = 0
    http_request_count = 0
    http_response_count = 0
    correlation_strong_count = 0
    correlation_probable_count = 0
    correlation_temporal_count = 0
    correlation_exact_count = 0
    anomaly_count = 0

    for event in reader.iter_events(
        identity.session_id,
        expected_identity=identity,
    ):
        event_count += 1
        source = event.get("source")
        event_type = event.get("event_type")

        if source in {"dom.action", "bas.action"} and event_type in {"dom.action", "bas.action"}:
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                action_ids.add(event_id)

        if source == "cdp.network":
            if event_type == "http.request":
                http_request_count += 1
            elif event_type == "http.response":
                http_response_count += 1
            elif event_type == "http.failed":
                anomaly_count += 1

        if source == "system" and event_type == "correlation.action_http":
            action_event_id = event.get("action_event_id")
            if isinstance(action_event_id, str) and action_event_id:
                correlated_action_ids.add(action_event_id)
            status = event.get("correlation_status")
            if status == "strong":
                correlation_strong_count += 1
            elif status == "probable":
                correlation_probable_count += 1
            elif status == "temporal-only":
                correlation_temporal_count += 1
            elif status == "exact":
                correlation_exact_count += 1

    return SessionSummary(
        session_id=identity.session_id,
        manifest_sha256=identity.manifest_sha256,
        evidence_sha256=identity.evidence_sha256,
        started_at=identity.started_at,
        ended_at=identity.ended_at,
        status=identity.status,
        event_count=event_count,
        action_count=len(action_ids),
        http_request_count=http_request_count,
        http_response_count=http_response_count,
        correlation_strong_count=correlation_strong_count,
        correlation_probable_count=correlation_probable_count,
        correlation_temporal_count=correlation_temporal_count,
        correlation_exact_count=correlation_exact_count,
        uncorrelated_action_count=len(action_ids - correlated_action_ids),
        warning_count=info.warning_count,
        anomaly_count=anomaly_count,
    )


def project_runtime_intelligence(
    evidence_reader: EvidenceReader,
    change_reader: ChangeSummaryReader | None = None,
    *,
    selected_sessions: tuple[str, ...] = (),
) -> RuntimeProjection:
    if not isinstance(evidence_reader, EvidenceReader):
        raise TypeError("evidence_reader must be EvidenceReader")
    if change_reader is not None and not isinstance(change_reader, ChangeSummaryReader):
        raise TypeError("change_reader must be ChangeSummaryReader or None")

    sessions = tuple(
        _session_summary(evidence_reader, info)
        for info in evidence_reader.iter_finalized_info(selected_sessions)
    )
    changes = (
        tuple(_change_record(summary) for summary in change_reader.iter_summaries())
        if change_reader is not None
        else ()
    )
    return RuntimeProjection(sessions=sessions, changes=changes)


__all__ = [
    "ChangeIndexRecord",
    "RUNTIME_PROJECTION_VERSION",
    "RuntimeProjection",
    "SessionSummary",
    "project_runtime_intelligence",
]
