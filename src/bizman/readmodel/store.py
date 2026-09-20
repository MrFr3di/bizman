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
from bizman.readmodel.model import (
    MatchKind,
    RefKind,
    SearchHit,
    SearchQuery,
    normalize_search_text,
)


INDEX_APPLICATION_ID = 0x424D4931
INDEX_SCHEMA_VERSION = 1
PROJECTION_VERSION = 2
_MAX_EVIDENCE_REFS_PER_HIT = 8
_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_EXPECTED_STRICT_TABLES = frozenset(
    {"ref", "knowledge_item", "alias", "knowledge_evidence", "index_meta"}
)
_EXPECTED_META_KEYS = frozenset(
    {
        "schema_version",
        "projection_version",
        "source_fingerprint",
        "generation",
        "item_count",
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
            "read-model metadata keys do not match schema v1 contract"
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
    except ValueError as exc:
        raise ReadModelIntegrityError("read-model item_count is not an integer") from exc
    if item_count < 0:
        raise ReadModelIntegrityError("read-model item_count must be non-negative")

    actual_item_count = int(connection.execute("SELECT COUNT(*) FROM ref").fetchone()[0])
    actual_fts_count = int(
        connection.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    )
    if actual_item_count != item_count or actual_fts_count != item_count:
        raise ReadModelIntegrityError(
            "read-model item counts disagree with persisted metadata"
        )

    expected_generation = canonical_sha256(
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "projection_version": PROJECTION_VERSION,
            "source_fingerprint": meta["source_fingerprint"],
            "item_count": item_count,
        }
    )
    if meta["generation"] != expected_generation:
        raise ReadModelIntegrityError(
            "read-model generation fingerprint does not match metadata"
        )
    _validate_completed_at(meta["completed_at"])


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


def _semantic_generation(projection: KnowledgeProjection) -> str:
    return canonical_sha256(
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "projection_version": PROJECTION_VERSION,
            "source_fingerprint": projection.source_fingerprint,
            "item_count": len(projection.records),
        }
    )


def _initialize(
    connection: sqlite3.Connection,
    projection: KnowledgeProjection,
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

    generation = _semantic_generation(projection)
    meta = {
        "schema_version": str(INDEX_SCHEMA_VERSION),
        "projection_version": str(PROJECTION_VERSION),
        "source_fingerprint": projection.source_fingerprint,
        "generation": generation,
        "item_count": str(len(projection.records)),
        "completed_at": completed_at,
    }
    connection.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?, ?)",
        tuple(sorted(meta.items())),
    )
    return generation


def rebuild_knowledge_index(
    database_path: Path,
    projection: KnowledgeProjection,
    *,
    completed_at: str,
) -> str:
    if not isinstance(projection, KnowledgeProjection):
        raise TypeError("projection must be KnowledgeProjection")
    completed = _validate_completed_at(completed_at)
    target = Path(database_path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bizman-index-build-", dir=target.parent) as temp_dir:
        staged = Path(temp_dir) / target.name
        connection = _connect(staged, read_only=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            generation = _initialize(connection, projection, completed_at=completed)
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
    "rebuild_knowledge_index",
]