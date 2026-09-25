from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Iterable

from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.knowledge import KnowledgeProjection
from bizman.readmodel.runtime import ChangeIndexRecord, RuntimeProjection, SessionSummary
from bizman.readmodel.model import (
    MatchKind,
    RefKind,
    SearchHit,
    SearchQuery,
    normalize_search_text,
)


INDEX_APPLICATION_ID = 0x424D4931
INDEX_SCHEMA_VERSION = 2
PROJECTION_VERSION = 3
_MAX_EVIDENCE_REFS_PER_HIT = 8
_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_EXPECTED_STRICT_TABLES = frozenset(
    {"ref", "knowledge_item", "alias", "knowledge_evidence", "session_summary", "change_index", "index_meta"}
)
_EXPECTED_META_KEYS = frozenset(
    {
        "schema_version",
        "projection_version",
        "source_fingerprint",
        "runtime_fingerprint",
        "generation",
        "item_count",
        "session_count",
        "change_count",
        "completed_at",
    }
)


class ReadModelError(RuntimeError):
    """Base class for deterministic read-model failures."""


class ReadModelCompatibilityError(ReadModelError):
    """Raised when a SQLite file is not a compatible BizMan read model."""


class ReadModelIntegrityError(ReadModelError):
    """Raised when persisted read-model invariants do not hold."""


_SCHEMA = (
    """
    CREATE TABLE ref (
        ref TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        source_dataset TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE knowledge_item (
        ref TEXT PRIMARY KEY REFERENCES ref(ref) ON DELETE CASCADE,
        title TEXT NOT NULL,
        normalized_title TEXT NOT NULL,
        body TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE alias (
        normalized_alias TEXT NOT NULL,
        alias TEXT NOT NULL,
        ref TEXT NOT NULL REFERENCES ref(ref) ON DELETE CASCADE,
        PRIMARY KEY (normalized_alias, ref)
    ) STRICT
    """,
    """
    CREATE TABLE knowledge_evidence (
        ref TEXT NOT NULL REFERENCES ref(ref) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        evidence_ref TEXT NOT NULL,
        PRIMARY KEY (ref, ordinal)
    ) STRICT
    """,
    """
    CREATE TABLE session_summary (
        session_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
        event_count INTEGER NOT NULL CHECK (event_count >= 0),
        action_count INTEGER NOT NULL CHECK (action_count >= 0),
        http_request_count INTEGER NOT NULL CHECK (http_request_count >= 0),
        http_response_count INTEGER NOT NULL CHECK (http_response_count >= 0),
        correlation_strong_count INTEGER NOT NULL CHECK (correlation_strong_count >= 0),
        correlation_probable_count INTEGER NOT NULL CHECK (correlation_probable_count >= 0),
        correlation_temporal_count INTEGER NOT NULL CHECK (correlation_temporal_count >= 0),
        correlation_exact_count INTEGER NOT NULL CHECK (correlation_exact_count >= 0),
        uncorrelated_action_count INTEGER NOT NULL CHECK (uncorrelated_action_count >= 0),
        warning_count INTEGER NOT NULL CHECK (warning_count >= 0),
        anomaly_count INTEGER NOT NULL CHECK (anomaly_count >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE change_index (
        analysis_profile_sha256 TEXT NOT NULL,
        change_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        rule_version INTEGER NOT NULL CHECK (rule_version > 0),
        kind TEXT NOT NULL,
        novelty_class TEXT NOT NULL,
        first_session_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_session_id TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
        PRIMARY KEY (analysis_profile_sha256, change_id)
    ) STRICT
    """,
    """
    CREATE TABLE index_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE VIRTUAL TABLE knowledge_fts USING fts5(
        ref UNINDEXED,
        title,
        aliases,
        body,
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
)


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve(strict=read_only)
    if read_only:
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=5.0)
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(resolved, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _runtime_projection_from_connection(connection: sqlite3.Connection) -> RuntimeProjection:
    sessions = tuple(
        SessionSummary(
            session_id=str(row["session_id"]),
            manifest_sha256=str(row["manifest_sha256"]),
            evidence_sha256=str(row["evidence_sha256"]),
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]),
            status=str(row["status"]),
            event_count=int(row["event_count"]),
            action_count=int(row["action_count"]),
            http_request_count=int(row["http_request_count"]),
            http_response_count=int(row["http_response_count"]),
            correlation_strong_count=int(row["correlation_strong_count"]),
            correlation_probable_count=int(row["correlation_probable_count"]),
            correlation_temporal_count=int(row["correlation_temporal_count"]),
            correlation_exact_count=int(row["correlation_exact_count"]),
            uncorrelated_action_count=int(row["uncorrelated_action_count"]),
            warning_count=int(row["warning_count"]),
            anomaly_count=int(row["anomaly_count"]),
        )
        for row in connection.execute(
            "SELECT * FROM session_summary ORDER BY session_id"
        )
    )
    changes = tuple(
        ChangeIndexRecord(
            analysis_profile_sha256=str(row["analysis_profile_sha256"]),
            change_id=str(row["change_id"]),
            rule_id=str(row["rule_id"]),
            rule_version=int(row["rule_version"]),
            kind=str(row["kind"]),
            novelty_class=str(row["novelty_class"]),
            first_session_id=str(row["first_session_id"]),
            first_seen_at=str(row["first_seen_at"]),
            last_session_id=str(row["last_session_id"]),
            last_seen_at=str(row["last_seen_at"]),
            occurrence_count=int(row["occurrence_count"]),
        )
        for row in connection.execute(
            """
            SELECT *
            FROM change_index
            ORDER BY analysis_profile_sha256, change_id
            """
        )
    )
    return RuntimeProjection(sessions=sessions, changes=changes)


def _validate_identity(connection: sqlite3.Connection) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if application_id != INDEX_APPLICATION_ID:
        raise ReadModelCompatibilityError(
            f"foreign read-model database application_id={application_id}"
        )
    if user_version != INDEX_SCHEMA_VERSION:
        raise ReadModelCompatibilityError(
            f"read-model schema {user_version} != supported {INDEX_SCHEMA_VERSION}"
        )

    table_rows = {
        str(row[1]): (str(row[2]).casefold(), int(row[5]))
        for row in connection.execute("PRAGMA table_list")
    }
    missing = _EXPECTED_STRICT_TABLES - set(table_rows)
    if missing:
        raise ReadModelCompatibilityError(
            "read-model database is missing required tables: "
            + ", ".join(sorted(missing))
        )
    if any(
        table_rows[name][0] != "table" or table_rows[name][1] != 1
        for name in _EXPECTED_STRICT_TABLES
    ):
        raise ReadModelCompatibilityError(
            "read-model ordinary schema tables must all be STRICT"
        )
    fts = table_rows.get("knowledge_fts")
    if fts is None or fts[0] != "virtual":
        raise ReadModelCompatibilityError(
            "read-model database is missing knowledge_fts virtual table"
        )

    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity.casefold() != "ok":
        raise ReadModelIntegrityError(f"SQLite integrity_check failed: {integrity}")

    meta = {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM index_meta")
    }
    if set(meta) != _EXPECTED_META_KEYS:
        raise ReadModelCompatibilityError(
            "read-model metadata keys do not match schema v2 contract"
        )
    if meta["schema_version"] != str(INDEX_SCHEMA_VERSION):
        raise ReadModelCompatibilityError(
            "read-model metadata schema_version does not match PRAGMA user_version"
        )
    if meta["projection_version"] != str(PROJECTION_VERSION):
        raise ReadModelCompatibilityError(
            "read-model projection version is unsupported"
        )
    try:
        item_count = int(meta["item_count"])
        session_count = int(meta["session_count"])
        change_count = int(meta["change_count"])
    except ValueError as exc:
        raise ReadModelIntegrityError("read-model counts must be integers") from exc
    if min(item_count, session_count, change_count) < 0:
        raise ReadModelIntegrityError("read-model counts must be non-negative")

    actual_item_count = int(connection.execute("SELECT COUNT(*) FROM ref").fetchone()[0])
    actual_fts_count = int(
        connection.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    )
    actual_session_count = int(
        connection.execute("SELECT COUNT(*) FROM session_summary").fetchone()[0]
    )
    actual_change_count = int(
        connection.execute("SELECT COUNT(*) FROM change_index").fetchone()[0]
    )
    if actual_item_count != item_count or actual_fts_count != item_count:
        raise ReadModelIntegrityError(
            "read-model item counts disagree with persisted metadata"
        )
    if actual_session_count != session_count or actual_change_count != change_count:
        raise ReadModelIntegrityError(
            "read-model runtime counts disagree with persisted metadata"
        )

    try:
        runtime = _runtime_projection_from_connection(connection)
    except (TypeError, ValueError) as exc:
        raise ReadModelIntegrityError(
            "read-model runtime rows violate projection invariants"
        ) from exc
    if runtime.source_fingerprint != meta["runtime_fingerprint"]:
        raise ReadModelIntegrityError(
            "read-model runtime fingerprint does not match persisted rows"
        )

    expected_generation = canonical_sha256(
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "projection_version": PROJECTION_VERSION,
            "source_fingerprint": meta["source_fingerprint"],
            "runtime_fingerprint": meta["runtime_fingerprint"],
            "item_count": item_count,
            "session_count": session_count,
            "change_count": change_count,
        }
    )
    if meta["generation"] != expected_generation:
        raise ReadModelIntegrityError(
            "read-model generation fingerprint does not match metadata"
        )
    try:
        _validate_completed_at(meta["completed_at"])
    except (TypeError, ValueError) as exc:
        raise ReadModelIntegrityError(
            "read-model completed_at violates metadata invariants"
        ) from exc


def _validate_completed_at(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("completed_at must be a non-empty RFC3339 timestamp")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("completed_at must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("completed_at must include a timezone offset")
    return value


def _semantic_generation(
    projection: KnowledgeProjection,
    runtime_projection: RuntimeProjection,
) -> str:
    return canonical_sha256(
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "projection_version": PROJECTION_VERSION,
            "source_fingerprint": projection.source_fingerprint,
            "runtime_fingerprint": runtime_projection.source_fingerprint,
            "item_count": len(projection.records),
            "session_count": len(runtime_projection.sessions),
            "change_count": len(runtime_projection.changes),
        }
    )


def _initialize(
    connection: sqlite3.Connection,
    projection: KnowledgeProjection,
    runtime_projection: RuntimeProjection,
    *,
    completed_at: str,
) -> str:
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.execute(f"PRAGMA application_id = {INDEX_APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")

    for record in projection.records:
        connection.execute(
            "INSERT INTO ref(ref, kind, source_dataset) VALUES (?, ?, ?)",
            (record.ref, record.kind.value, record.source_dataset),
        )
        connection.execute(
            "INSERT INTO knowledge_item(ref, title, normalized_title, body) VALUES (?, ?, ?, ?)",
            (
                record.ref,
                record.title,
                normalize_search_text(record.title),
                record.body,
            ),
        )
        for alias in record.aliases:
            connection.execute(
                "INSERT INTO alias(normalized_alias, alias, ref) VALUES (?, ?, ?)",
                (normalize_search_text(alias), alias, record.ref),
            )
        for ordinal, evidence_ref in enumerate(record.evidence_refs):
            connection.execute(
                "INSERT INTO knowledge_evidence(ref, ordinal, evidence_ref) VALUES (?, ?, ?)",
                (record.ref, ordinal, evidence_ref),
            )
        connection.execute(
            "INSERT INTO knowledge_fts(ref, title, aliases, body) VALUES (?, ?, ?, ?)",
            (
                record.ref,
                record.title,
                " ".join(record.aliases),
                record.body,
            ),
        )

    for item in runtime_projection.sessions:
        connection.execute(
            """
            INSERT INTO session_summary(
                session_id, manifest_sha256, evidence_sha256, started_at, ended_at,
                status, event_count, action_count, http_request_count,
                http_response_count, correlation_strong_count,
                correlation_probable_count, correlation_temporal_count,
                correlation_exact_count, uncorrelated_action_count, warning_count,
                anomaly_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.session_id,
                item.manifest_sha256,
                item.evidence_sha256,
                item.started_at,
                item.ended_at,
                item.status,
                item.event_count,
                item.action_count,
                item.http_request_count,
                item.http_response_count,
                item.correlation_strong_count,
                item.correlation_probable_count,
                item.correlation_temporal_count,
                item.correlation_exact_count,
                item.uncorrelated_action_count,
                item.warning_count,
                item.anomaly_count,
            ),
        )

    for item in runtime_projection.changes:
        connection.execute(
            """
            INSERT INTO change_index(
                analysis_profile_sha256, change_id, rule_id, rule_version, kind,
                novelty_class, first_session_id, first_seen_at, last_session_id,
                last_seen_at, occurrence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.analysis_profile_sha256,
                item.change_id,
                item.rule_id,
                item.rule_version,
                item.kind,
                item.novelty_class,
                item.first_session_id,
                item.first_seen_at,
                item.last_session_id,
                item.last_seen_at,
                item.occurrence_count,
            ),
        )

    generation = _semantic_generation(projection, runtime_projection)
    meta = {
        "schema_version": str(INDEX_SCHEMA_VERSION),
        "projection_version": str(PROJECTION_VERSION),
        "source_fingerprint": projection.source_fingerprint,
        "runtime_fingerprint": runtime_projection.source_fingerprint,
        "generation": generation,
        "item_count": str(len(projection.records)),
        "session_count": str(len(runtime_projection.sessions)),
        "change_count": str(len(runtime_projection.changes)),
        "completed_at": completed_at,
    }
    connection.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?, ?)",
        tuple(sorted(meta.items())),
    )
    return generation


def rebuild_agent_index(
    database_path: Path,
    projection: KnowledgeProjection,
    runtime_projection: RuntimeProjection,
    *,
    completed_at: str,
) -> str:
    if not isinstance(projection, KnowledgeProjection):
        raise TypeError("projection must be KnowledgeProjection")
    if not isinstance(runtime_projection, RuntimeProjection):
        raise TypeError("runtime_projection must be RuntimeProjection")
    completed = _validate_completed_at(completed_at)
    target = Path(database_path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bizman-index-build-", dir=target.parent) as temp_dir:
        staged = Path(temp_dir) / target.name
        connection = _connect(staged, read_only=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            generation = _initialize(
                connection,
                projection,
                runtime_projection,
                completed_at=completed,
            )
            connection.execute("COMMIT")
            _validate_identity(connection)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        os.replace(staged, target)
    return generation


def rebuild_knowledge_index(
    database_path: Path,
    projection: KnowledgeProjection,
    *,
    completed_at: str,
    runtime_projection: RuntimeProjection | None = None,
) -> str:
    """Compatibility entry point; runtime data defaults to an empty projection."""

    runtime = runtime_projection if runtime_projection is not None else RuntimeProjection()
    return rebuild_agent_index(
        database_path,
        projection,
        runtime,
        completed_at=completed_at,
    )


def _fts_query(text: str) -> str | None:
    tokens = _FTS_TOKEN_RE.findall(normalize_search_text(text))
    if not tokens:
        return None
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


class KnowledgeIndex:
    def __init__(self, database_path: Path):
        self.path = Path(database_path).expanduser().resolve(strict=True)
        self._connection = _connect(self.path, read_only=True)
        try:
            _validate_identity(self._connection)
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "KnowledgeIndex":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def metadata(self) -> dict[str, str]:
        return {
            str(row["key"]): str(row["value"])
            for row in self._connection.execute(
                "SELECT key, value FROM index_meta ORDER BY key"
            )
        }

    def session_summaries(self) -> tuple[SessionSummary, ...]:
        return _runtime_projection_from_connection(self._connection).sessions

    def change_records(
        self,
        analysis_profile_sha256: str | None = None,
    ) -> tuple[ChangeIndexRecord, ...]:
        runtime = _runtime_projection_from_connection(self._connection)
        if analysis_profile_sha256 is None:
            return runtime.changes
        if (
            not isinstance(analysis_profile_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", analysis_profile_sha256) is None
        ):
            raise ValueError(
                "analysis_profile_sha256 must be 64 lowercase hexadecimal characters"
            )
        return tuple(
            item
            for item in runtime.changes
            if item.analysis_profile_sha256 == analysis_profile_sha256
        )

    def _kind_clause(
        self,
        kinds: tuple[RefKind, ...],
        *,
        prefix: str = "r",
    ) -> tuple[str, tuple[str, ...]]:
        if not kinds:
            return "", ()
        placeholders = ",".join("?" for _ in kinds)
        return (
            f" AND {prefix}.kind IN ({placeholders})",
            tuple(kind.value for kind in kinds),
        )

    def _evidence(self, ref: str) -> tuple[str, ...]:
        return tuple(
            str(row["evidence_ref"])
            for row in self._connection.execute(
                """
                SELECT evidence_ref
                FROM knowledge_evidence
                WHERE ref = ?
                ORDER BY ordinal
                LIMIT ?
                """,
                (ref, _MAX_EVIDENCE_REFS_PER_HIT),
            )
        )

    def _hit(self, row: sqlite3.Row, match_kind: MatchKind) -> SearchHit:
        return SearchHit(
            ref=str(row["ref"]),
            kind=RefKind(str(row["kind"])),
            title=str(row["title"]),
            match_kind=match_kind,
            evidence_refs=self._evidence(str(row["ref"])),
        )

    def _append_rows(
        self,
        hits: list[SearchHit],
        seen: set[str],
        rows: Iterable[sqlite3.Row],
        match_kind: MatchKind,
        *,
        limit: int,
    ) -> None:
        for row in rows:
            ref = str(row["ref"])
            if ref in seen:
                continue
            seen.add(ref)
            hits.append(self._hit(row, match_kind))
            if len(hits) >= limit:
                return

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        if not isinstance(query, SearchQuery):
            raise TypeError("query must be SearchQuery")
        normalized = normalize_search_text(query.text)
        hits: list[SearchHit] = []
        seen: set[str] = set()

        kind_sql, kind_values = self._kind_clause(query.kinds)

        rows = self._connection.execute(
            """
            SELECT r.ref, r.kind, i.title
            FROM ref AS r
            JOIN knowledge_item AS i ON i.ref = r.ref
            WHERE r.ref = ?
            """ + kind_sql + " ORDER BY r.ref",
            (query.text, *kind_values),
        )
        self._append_rows(
            hits, seen, rows, MatchKind.EXACT_REF, limit=query.limit
        )
        if len(hits) >= query.limit:
            return tuple(hits)

        rows = self._connection.execute(
            """
            SELECT r.ref, r.kind, i.title
            FROM alias AS a
            JOIN ref AS r ON r.ref = a.ref
            JOIN knowledge_item AS i ON i.ref = r.ref
            WHERE a.normalized_alias = ?
            """ + kind_sql + " ORDER BY r.ref",
            (normalized, *kind_values),
        )
        self._append_rows(
            hits, seen, rows, MatchKind.EXACT_ALIAS, limit=query.limit
        )
        if len(hits) >= query.limit:
            return tuple(hits)

        rows = self._connection.execute(
            """
            SELECT r.ref, r.kind, i.title
            FROM knowledge_item AS i
            JOIN ref AS r ON r.ref = i.ref
            WHERE i.normalized_title = ?
            """ + kind_sql + " ORDER BY r.ref",
            (normalized, *kind_values),
        )
        self._append_rows(
            hits, seen, rows, MatchKind.EXACT_TITLE, limit=query.limit
        )
        if len(hits) >= query.limit:
            return tuple(hits)

        expression = _fts_query(query.text)
        if expression is None:
            return tuple(hits)

        remaining = query.limit - len(hits)
        fts_kind_sql, fts_kind_values = self._kind_clause(query.kinds)
        rows = self._connection.execute(
            """
            SELECT r.ref, r.kind, i.title, bm25(knowledge_fts) AS score
            FROM knowledge_fts
            JOIN ref AS r ON r.ref = knowledge_fts.ref
            JOIN knowledge_item AS i ON i.ref = r.ref
            WHERE knowledge_fts MATCH ?
            """ + fts_kind_sql + """
            ORDER BY score, r.ref
            LIMIT ?
            """,
            (expression, *fts_kind_values, remaining + len(seen)),
        )
        self._append_rows(
            hits, seen, rows, MatchKind.FULL_TEXT, limit=query.limit
        )
        return tuple(hits)


__all__ = [
    "INDEX_APPLICATION_ID",
    "INDEX_SCHEMA_VERSION",
    "KnowledgeIndex",
    "ReadModelCompatibilityError",
    "ReadModelError",
    "ReadModelIntegrityError",
    "rebuild_agent_index",
    "rebuild_knowledge_index",
]