from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import sqlite3

from bizman.core.context import CoreContext
from bizman.core.errors import (
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
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
_CURSOR_VERSION = 1
_CURSOR_PREFIX = "bmcur1."
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_KNOWLEDGE_KINDS = frozenset(kind.value for kind in RefKind)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty string")
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


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_cursor(*, kind: str, scope: str, key: tuple[str, str]) -> str:
    payload = {
        "kind": kind,
        "scope": scope,
        "key": list(key),
        "version": _CURSOR_VERSION,
    }
    payload_bytes = _canonical_json(payload)
    envelope = {
        "payload": payload,
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    token = base64.urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")
    return _CURSOR_PREFIX + token


def _decode_cursor(
    cursor: str,
    *,
    kind: str,
    scope: str,
) -> tuple[str, str]:
    _require_text(cursor, name="cursor")
    if not cursor.startswith(_CURSOR_PREFIX):
        raise ValueError("cursor has an unsupported format")
    encoded = cursor[len(_CURSOR_PREFIX) :]
    if not encoded:
        raise ValueError("cursor payload is empty")
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(
            (encoded + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is malformed") from exc
    canonical_encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if not hmac.compare_digest(encoded, canonical_encoded):
        raise ValueError("cursor encoding is not canonical")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise ValueError("cursor envelope is invalid")
    payload = envelope["payload"]
    checksum = envelope["sha256"]
    if (
        not isinstance(payload, dict)
        or set(payload) != {"kind", "scope", "key", "version"}
        or not isinstance(checksum, str)
        or _SHA256_RE.fullmatch(checksum) is None
    ):
        raise ValueError("cursor envelope is invalid")
    expected = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if not hmac.compare_digest(checksum, expected):
        raise ValueError("cursor integrity check failed")
    if payload["version"] != _CURSOR_VERSION:
        raise ValueError("cursor version is unsupported")
    if payload["kind"] != kind or payload["scope"] != scope:
        raise ValueError("cursor does not belong to this query")
    key = payload["key"]
    if (
        not isinstance(key, list)
        or len(key) != 2
        or not all(isinstance(value, str) and value for value in key)
    ):
        raise ValueError("cursor key is invalid")
    return key[0], key[1]


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
            _decode_cursor(self.cursor, kind="sessions", scope="*")


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
            _decode_cursor(self.cursor, kind="changes", scope=scope)


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
    after = (
        _decode_cursor(request.cursor, kind="sessions", scope="*")
        if request.cursor is not None
        else None
    )
    with _agent_index(context) as index:
        values, has_more = index.session_page(limit=request.limit, after=after)
    items = tuple(_session_record(item) for item in values)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            kind="sessions",
            scope="*",
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


def list_changes(
    context: CoreContext,
    request: ChangeListRequest,
) -> ChangePage:
    if not isinstance(request, ChangeListRequest):
        raise TypeError("request must be ChangeListRequest")
    profile = request.analysis_profile_sha256
    scope = profile if profile is not None else "*"
    after = (
        _decode_cursor(request.cursor, kind="changes", scope=scope)
        if request.cursor is not None
        else None
    )
    with _agent_index(context) as index:
        values, has_more = index.change_page(
            limit=request.limit,
            analysis_profile_sha256=profile,
            after=after,
        )
    items = tuple(_change_record(item) for item in values)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            kind="changes",
            scope=scope,
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
    "SessionGetRequest",
    "SessionGetResult",
    "SessionListRequest",
    "SessionPage",
    "SessionRecord",
    "get_change",
    "get_knowledge",
    "get_session",
    "list_changes",
    "list_sessions",
    "resolve_knowledge",
    "search_knowledge",
]
