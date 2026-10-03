from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import re
import sqlite3

from bizman.core.context import CoreContext
from bizman.core.errors import (
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.core.pagination import decode_cursor, encode_cursor
from bizman.readmodel.model import KnowledgeRecord as ReadKnowledgeRecord
from bizman.readmodel.model import RefKind, SearchHit as ReadSearchHit, SearchQuery
from bizman.readmodel.runtime import (
    ChangeIndexRecord as ReadChangeRecord,
    SessionSummary as ReadSessionRecord,
)
from bizman.readmodel.store import (
    KnowledgeIndex,
    ReadModelCompatibilityError,
    ReadModelError,
    ReadModelIntegrityError,
)


_MAX_QUERY_LENGTH = 512
_MAX_LIMIT = 50
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_KNOWLEDGE_KINDS = frozenset(kind.value for kind in RefKind)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_query(value: object) -> str:
    text = _require_text(value, name="text").strip()
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > _MAX_QUERY_LENGTH:
        raise ValueError(f"text exceeds {_MAX_QUERY_LENGTH} characters")
    return text


def _require_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("limit must be an integer")
    if not 1 <= value <= _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
    return value


def _require_profile(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(
            "analysis_profile_sha256 must be 64 lowercase hexadecimal characters"
        )
    return value


def _require_session_id(value: object) -> str:
    if not isinstance(value, str) or _SESSION_ID_RE.fullmatch(value) is None:
        raise ValueError("session_id must be a canonical UUIDv7")
    return value


def _require_instant(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return text


def _normalize_kinds(values: object) -> tuple[str, ...]:
    if isinstance(values, list):
        values = tuple(values)
    if not isinstance(values, tuple):
        raise TypeError("kinds must be a tuple or list of strings")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or value not in _KNOWLEDGE_KINDS:
            raise ValueError(f"unsupported knowledge kind: {value!r}")
        if value not in result:
            result.append(value)
    return tuple(result)


def _kind_values(values: tuple[str, ...]) -> tuple[RefKind, ...]:
    return tuple(RefKind(value) for value in values)


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    ref: str
    kind: str
    title: str
    match_kind: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    ref: str
    kind: str
    title: str
    aliases: tuple[str, ...]
    body: str
    evidence_refs: tuple[str, ...]
    source_dataset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class KnowledgeResolveRequest:
    text: str
    kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_query(self.text))
        object.__setattr__(self, "kinds", _normalize_kinds(self.kinds))


@dataclass(frozen=True, slots=True)
class KnowledgeResolveResult:
    hit: KnowledgeHit | None


@dataclass(frozen=True, slots=True)
class KnowledgeSearchRequest:
    text: str
    limit: int = 20
    kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _require_query(self.text))
        _require_limit(self.limit)
        object.__setattr__(self, "kinds", _normalize_kinds(self.kinds))


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    items: tuple[KnowledgeHit, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class KnowledgeGetRequest:
    ref: str

    def __post_init__(self) -> None:
        _require_text(self.ref, name="ref")


@dataclass(frozen=True, slots=True)
class KnowledgeGetResult:
    item: KnowledgeItem | None


@dataclass(frozen=True, slots=True)
class SessionRecord:
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


@dataclass(frozen=True, slots=True)
class SessionListRequest:
    limit: int = 20
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        if self.cursor is not None:
            _, started_at, session_id = decode_cursor(
                self.cursor,
                kind="sessions",
                scope="*",
            )
            _require_instant(started_at, name="session cursor started_at")
            _require_session_id(session_id)


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[SessionRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class SessionGetRequest:
    session_id: str

    def __post_init__(self) -> None:
        _require_session_id(self.session_id)


@dataclass(frozen=True, slots=True)
class SessionGetResult:
    session: SessionRecord | None


@dataclass(frozen=True, slots=True)
class SessionCompareRequest:
    from_session_id: str
    to_session_id: str

    def __post_init__(self) -> None:
        _require_session_id(self.from_session_id)
        _require_session_id(self.to_session_id)


@dataclass(frozen=True, slots=True)
class SessionComparison:
    from_session_id: str
    from_started_at: str
    from_ended_at: str
    from_status: str
    to_session_id: str
    to_started_at: str
    to_ended_at: str
    to_status: str
    event_count_delta: int
    action_count_delta: int
    http_request_count_delta: int
    http_response_count_delta: int
    correlation_strong_count_delta: int
    correlation_probable_count_delta: int
    correlation_temporal_count_delta: int
    correlation_exact_count_delta: int
    uncorrelated_action_count_delta: int
    warning_count_delta: int
    anomaly_count_delta: int


@dataclass(frozen=True, slots=True)
class SessionCompareResult:
    comparison: SessionComparison | None
    missing_session_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = tuple(self.missing_session_ids)
        if len(missing) != len(set(missing)):
            raise ValueError("missing_session_ids must be unique")
        for session_id in missing:
            _require_session_id(session_id)
        if self.comparison is None and not missing:
            raise ValueError("missing comparison requires missing_session_ids")
        if self.comparison is not None and missing:
            raise ValueError("successful comparison cannot report missing_session_ids")
        object.__setattr__(self, "missing_session_ids", missing)


@dataclass(frozen=True, slots=True)
class SessionAnomalyRecord:
    session_id: str
    started_at: str
    ended_at: str
    status: str
    warning_count: int
    anomaly_count: int
    uncorrelated_action_count: int


@dataclass(frozen=True, slots=True)
class SessionAnomalyListRequest:
    limit: int = 20
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        if self.cursor is not None:
            _, started_at, session_id = decode_cursor(
                self.cursor,
                kind="session-anomalies",
                scope="*",
            )
            _require_instant(started_at, name="session anomaly cursor started_at")
            _require_session_id(session_id)


@dataclass(frozen=True, slots=True)
class SessionAnomalyPage:
    items: tuple[SessionAnomalyRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class ChangeRecord:
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


@dataclass(frozen=True, slots=True)
class ChangeListRequest:
    limit: int = 20
    analysis_profile_sha256: str | None = None
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        profile = _require_profile(self.analysis_profile_sha256, optional=True)
        scope = profile if profile is not None else "*"
        if self.cursor is not None:
            _, cursor_profile, change_id = decode_cursor(
                self.cursor,
                kind="changes",
                scope=scope,
            )
            _require_profile(cursor_profile)
            _require_text(change_id, name="change cursor change_id")
            if profile is not None and cursor_profile != profile:
                raise ValueError("change cursor key does not match profile filter")


@dataclass(frozen=True, slots=True)
class ChangePage:
    items: tuple[ChangeRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class ChangeGetRequest:
    analysis_profile_sha256: str
    change_id: str

    def __post_init__(self) -> None:
        _require_profile(self.analysis_profile_sha256)
        _require_text(self.change_id, name="change_id")


@dataclass(frozen=True, slots=True)
class ChangeGetResult:
    change: ChangeRecord | None


def _knowledge_hit(item: ReadSearchHit) -> KnowledgeHit:
    return KnowledgeHit(
        ref=item.ref,
        kind=item.kind.value,
        title=item.title,
        match_kind=item.match_kind.value,
        evidence_refs=item.evidence_refs,
    )


def _knowledge_item(item: ReadKnowledgeRecord) -> KnowledgeItem:
    return KnowledgeItem(
        ref=item.ref,
        kind=item.kind.value,
        title=item.title,
        aliases=item.aliases,
        body=item.body,
        evidence_refs=item.evidence_refs,
        source_dataset=item.source_dataset,
    )


def _session_record(item: ReadSessionRecord) -> SessionRecord:
    return SessionRecord(
        **{
            field: getattr(item, field)
            for field in SessionRecord.__dataclass_fields__
        }
    )


def _session_anomaly_record(item: ReadSessionRecord) -> SessionAnomalyRecord:
    return SessionAnomalyRecord(
        session_id=item.session_id,
        started_at=item.started_at,
        ended_at=item.ended_at,
        status=item.status,
        warning_count=item.warning_count,
        anomaly_count=item.anomaly_count,
        uncorrelated_action_count=item.uncorrelated_action_count,
    )


def _session_comparison(
    before: ReadSessionRecord,
    after: ReadSessionRecord,
) -> SessionComparison:
    return SessionComparison(
        from_session_id=before.session_id,
        from_started_at=before.started_at,
        from_ended_at=before.ended_at,
        from_status=before.status,
        to_session_id=after.session_id,
        to_started_at=after.started_at,
        to_ended_at=after.ended_at,
        to_status=after.status,
        event_count_delta=after.event_count - before.event_count,
        action_count_delta=after.action_count - before.action_count,
        http_request_count_delta=(
            after.http_request_count - before.http_request_count
        ),
        http_response_count_delta=(
            after.http_response_count - before.http_response_count
        ),
        correlation_strong_count_delta=(
            after.correlation_strong_count - before.correlation_strong_count
        ),
        correlation_probable_count_delta=(
            after.correlation_probable_count - before.correlation_probable_count
        ),
        correlation_temporal_count_delta=(
            after.correlation_temporal_count - before.correlation_temporal_count
        ),
        correlation_exact_count_delta=(
            after.correlation_exact_count - before.correlation_exact_count
        ),
        uncorrelated_action_count_delta=(
            after.uncorrelated_action_count - before.uncorrelated_action_count
        ),
        warning_count_delta=after.warning_count - before.warning_count,
        anomaly_count_delta=after.anomaly_count - before.anomaly_count,
    )


def _change_record(item: ReadChangeRecord) -> ChangeRecord:
    return ChangeRecord(
        **{
            field: getattr(item, field)
            for field in ChangeRecord.__dataclass_fields__
        }
    )


@contextmanager
def _agent_index(context: CoreContext) -> Iterator[KnowledgeIndex]:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    index: KnowledgeIndex | None = None
    try:
        index = KnowledgeIndex(context.data_dir / "index" / "agent-index.sqlite3")
        yield index
    except FileNotFoundError as exc:
        raise ConfigurationError(
            "Agent Index is unavailable; build or rebuild agent-index.sqlite3"
        ) from exc
    except ReadModelCompatibilityError as exc:
        raise ContractMismatchError("Agent Index contract is incompatible") from exc
    except ReadModelIntegrityError as exc:
        raise DataIntegrityError("Agent Index failed integrity validation") from exc
    except ReadModelError as exc:
        raise OperationError("Agent Index read operation failed") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OperationError("Agent Index read operation failed") from exc
    finally:
        if index is not None:
            index.close()


def resolve_knowledge(
    context: CoreContext,
    request: KnowledgeResolveRequest,
) -> KnowledgeResolveResult:
    if not isinstance(request, KnowledgeResolveRequest):
        raise TypeError("request must be KnowledgeResolveRequest")
    with _agent_index(context) as index:
        hits = index.search(
            SearchQuery(
                text=request.text,
                limit=1,
                kinds=_kind_values(request.kinds),
            )
        )
    return KnowledgeResolveResult(hit=_knowledge_hit(hits[0]) if hits else None)


def search_knowledge(
    context: CoreContext,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResult:
    if not isinstance(request, KnowledgeSearchRequest):
        raise TypeError("request must be KnowledgeSearchRequest")
    with _agent_index(context) as index:
        hits = index.search(
            SearchQuery(
                text=request.text,
                limit=request.limit,
                kinds=_kind_values(request.kinds),
            )
        )
    return KnowledgeSearchResult(items=tuple(_knowledge_hit(item) for item in hits))


def get_knowledge(
    context: CoreContext,
    request: KnowledgeGetRequest,
) -> KnowledgeGetResult:
    if not isinstance(request, KnowledgeGetRequest):
        raise TypeError("request must be KnowledgeGetRequest")
    with _agent_index(context) as index:
        item = index.knowledge_record(request.ref)
    return KnowledgeGetResult(item=_knowledge_item(item) if item is not None else None)


def list_sessions(
    context: CoreContext,
    request: SessionListRequest,
) -> SessionPage:
    if not isinstance(request, SessionListRequest):
        raise TypeError("request must be SessionListRequest")
    cursor_state = (
        decode_cursor(request.cursor, kind="sessions", scope="*")
        if request.cursor is not None
        else None
    )
    cursor_generation = cursor_state[0] if cursor_state is not None else None
    after = (
        (cursor_state[1], cursor_state[2])
        if cursor_state is not None
        else None
    )
    with _agent_index(context) as index:
        generation = index.metadata()["generation"]
        if cursor_generation is not None and cursor_generation != generation:
            raise ValueError("cursor generation does not match current Agent Index")
        values, has_more = index.session_page(limit=request.limit, after=after)
    items = tuple(_session_record(item) for item in values)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            kind="sessions",
            scope="*",
            generation=generation,
            key=(last.started_at, last.session_id),
        )
    return SessionPage(items=items, next_cursor=next_cursor)


def get_session(
    context: CoreContext,
    request: SessionGetRequest,
) -> SessionGetResult:
    if not isinstance(request, SessionGetRequest):
        raise TypeError("request must be SessionGetRequest")
    with _agent_index(context) as index:
        item = index.session_record(request.session_id)
    return SessionGetResult(
        session=_session_record(item) if item is not None else None
    )


def compare_sessions(
    context: CoreContext,
    request: SessionCompareRequest,
) -> SessionCompareResult:
    if not isinstance(request, SessionCompareRequest):
        raise TypeError("request must be SessionCompareRequest")
    with _agent_index(context) as index:
        before = index.session_record(request.from_session_id)
        after = index.session_record(request.to_session_id)

    missing: list[str] = []
    if before is None:
        missing.append(request.from_session_id)
    if after is None and request.to_session_id not in missing:
        missing.append(request.to_session_id)
    if missing:
        return SessionCompareResult(
            comparison=None,
            missing_session_ids=tuple(missing),
        )

    assert before is not None
    assert after is not None
    return SessionCompareResult(
        comparison=_session_comparison(before, after),
    )


def list_session_anomalies(
    context: CoreContext,
    request: SessionAnomalyListRequest,
) -> SessionAnomalyPage:
    if not isinstance(request, SessionAnomalyListRequest):
        raise TypeError("request must be SessionAnomalyListRequest")
    cursor_state = (
        decode_cursor(
            request.cursor,
            kind="session-anomalies",
            scope="*",
        )
        if request.cursor is not None
        else None
    )
    cursor_generation = cursor_state[0] if cursor_state is not None else None
    after = (
        (cursor_state[1], cursor_state[2])
        if cursor_state is not None
        else None
    )
    with _agent_index(context) as index:
        generation = index.metadata()["generation"]
        if cursor_generation is not None and cursor_generation != generation:
            raise ValueError("cursor generation does not match current Agent Index")
        values, has_more = index.session_signal_page(
            limit=request.limit,
            after=after,
        )
    items = tuple(_session_anomaly_record(item) for item in values)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            kind="session-anomalies",
            scope="*",
            generation=generation,
            key=(last.started_at, last.session_id),
        )
    return SessionAnomalyPage(items=items, next_cursor=next_cursor)


def list_changes(
    context: CoreContext,
    request: ChangeListRequest,
) -> ChangePage:
    if not isinstance(request, ChangeListRequest):
        raise TypeError("request must be ChangeListRequest")
    profile = request.analysis_profile_sha256
    scope = profile if profile is not None else "*"
    cursor_state = (
        decode_cursor(request.cursor, kind="changes", scope=scope)
        if request.cursor is not None
        else None
    )
    cursor_generation = cursor_state[0] if cursor_state is not None else None
    after = (
        (cursor_state[1], cursor_state[2])
        if cursor_state is not None
        else None
    )
    with _agent_index(context) as index:
        generation = index.metadata()["generation"]
        if cursor_generation is not None and cursor_generation != generation:
            raise ValueError("cursor generation does not match current Agent Index")
        values, has_more = index.change_page(
            limit=request.limit,
            analysis_profile_sha256=profile,
            after=after,
        )
    items = tuple(_change_record(item) for item in values)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            kind="changes",
            scope=scope,
            generation=generation,
            key=(last.analysis_profile_sha256, last.change_id),
        )
    return ChangePage(items=items, next_cursor=next_cursor)


def get_change(
    context: CoreContext,
    request: ChangeGetRequest,
) -> ChangeGetResult:
    if not isinstance(request, ChangeGetRequest):
        raise TypeError("request must be ChangeGetRequest")
    with _agent_index(context) as index:
        item = index.change_record(
            request.analysis_profile_sha256,
            request.change_id,
        )
    return ChangeGetResult(
        change=_change_record(item) if item is not None else None
    )


__all__ = [
    "ChangeGetRequest",
    "ChangeGetResult",
    "ChangeListRequest",
    "ChangePage",
    "ChangeRecord",
    "KnowledgeGetRequest",
    "KnowledgeGetResult",
    "KnowledgeHit",
    "KnowledgeItem",
    "KnowledgeResolveRequest",
    "KnowledgeResolveResult",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "SessionAnomalyListRequest",
    "SessionAnomalyPage",
    "SessionAnomalyRecord",
    "SessionCompareRequest",
    "SessionCompareResult",
    "SessionComparison",
    "SessionGetRequest",
    "SessionGetResult",
    "SessionListRequest",
    "SessionPage",
    "SessionRecord",
    "compare_sessions",
    "get_change",
    "get_knowledge",
    "get_session",
    "list_changes",
    "list_session_anomalies",
    "list_sessions",
    "resolve_knowledge",
    "search_knowledge",
]
