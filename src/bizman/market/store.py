from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from typing import Final

from bizman.foundation.fingerprint import canonical_sha256


APPLICATION_ID: Final[int] = 0x424D4B31
USER_VERSION: Final[int] = 1
MIN_SQLITE_VERSION: Final[tuple[int, int, int]] = (3, 37, 0)
SURFACES: Final[frozenset[str]] = frozenset(
    {
        "retailmarket.city",
        "retailprices.group",
        "vendors",
    }
)
MAX_READ_LIMIT: Final[int] = 1000
DEFAULT_READ_LIMIT: Final[int] = 100

_APPEND_ONLY_MESSAGE = "market observations are append-only"
_MAX_SIGNED_INT64 = (1 << 63) - 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_EXPECTED_TABLES = frozenset({"observation"})
_REQUIRED_TRIGGERS = frozenset(
    {
        "observation_no_update",
        "observation_no_delete",
    }
)
_SURFACE_LIST_SQL = ", ".join(f"'{surface}'" for surface in sorted(SURFACES))


class MarketStoreError(Exception):
    """Base class for market store storage failures."""


class MarketStoreCompatibilityError(MarketStoreError):
    """The SQLite file/runtime is incompatible with the market store schema."""


class MarketStoreIntegrityError(MarketStoreError):
    """Persisted market observations violate deterministic identity."""


class MarketStoreOperationError(MarketStoreError):
    """A market store SQLite operation failed."""


class MarketStoreConflictError(MarketStoreError):
    """An append duplicates stored market observation content."""


_SCHEMA = (
    f"""
    CREATE TABLE observation (
        observation_id TEXT PRIMARY KEY,
        surface TEXT NOT NULL,
        request_key TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        source_session_id TEXT NOT NULL,
        source_sequence INTEGER NOT NULL,
        evidence_ref TEXT NOT NULL,
        response_text_sha256 TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        payload TEXT NOT NULL,
        CHECK (length(observation_id) = 36),
        CHECK (surface IN ({_SURFACE_LIST_SQL})),
        CHECK (source_sequence >= 0),
        CHECK (length(response_text_sha256) = 64),
        CHECK (length(artifact_sha256) = 64)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX observation_content_unique
    ON observation(surface, request_key, captured_at, response_text_sha256)
    """,
    f"""
    CREATE TRIGGER observation_no_update
    BEFORE UPDATE ON observation
    BEGIN
        SELECT RAISE(ABORT, '{_APPEND_ONLY_MESSAGE}');
    END
    """,
    f"""
    CREATE TRIGGER observation_no_delete
    BEFORE DELETE ON observation
    BEGIN
        SELECT RAISE(ABORT, '{_APPEND_ONLY_MESSAGE}');
    END
    """,
)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_uuid7(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _UUID7_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical UUIDv7")
    return value


def _require_surface(value: object) -> str:
    text = _require_text(value, name="surface")
    if text not in SURFACES:
        raise ValueError("surface must be a known market surface")
    return text


def _canonical_instant(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _non_negative_int(value: object, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SIGNED_INT64
    ):
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _trigger_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger'"
        )
    }


def _preflight_rw_identity(connection: sqlite3.Connection) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables = _user_tables(connection)

    if application_id == 0:
        if user_version == 0 and not tables:
            return
        raise MarketStoreCompatibilityError(
            "unidentified non-empty SQLite database cannot be adopted as a market store"
        )
    if application_id != APPLICATION_ID:
        raise MarketStoreCompatibilityError(
            f"foreign market store database application_id={application_id}"
        )
    if user_version != USER_VERSION:
        relation = "newer" if user_version > USER_VERSION else "older"
        raise MarketStoreCompatibilityError(
            f"{relation} market store schema {user_version} is unsupported; "
            f"expected {USER_VERSION}"
        )


def _connect_rw(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        raise MarketStoreCompatibilityError(
            "market store requires SQLite >= 3.37.0 for STRICT tables"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None leaves BEGIN/COMMIT under the store's control on
        # both supported Python versions without relying on 3.12+ keywords.
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            _preflight_rw_identity(connection)
            mode = str(
                connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            ).casefold()
            if mode != "wal":
                raise MarketStoreCompatibilityError(
                    "market store requires SQLite WAL journal mode"
                )
            connection.execute("PRAGMA synchronous = NORMAL")
            return connection
        except BaseException:
            connection.close()
            raise
    except MarketStoreError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise MarketStoreOperationError(
            "cannot open market store database for writing"
        ) from exc


def _connect_ro(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        raise MarketStoreCompatibilityError(
            "market store requires SQLite >= 3.37.0 for STRICT tables"
        )
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(
            uri,
            timeout=5.0,
            uri=True,
            isolation_level=None,
        )
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise MarketStoreOperationError(
            "cannot open market store database read-only"
        ) from exc


@dataclass(frozen=True, slots=True)
class MarketObservationRecord:
    observation_id: str
    surface: str
    request_key: str
    captured_at: str
    source_session_id: str
    source_sequence: int
    evidence_ref: str
    response_text_sha256: str
    artifact_sha256: str
    payload: str

    def __post_init__(self) -> None:
        _require_uuid7(self.observation_id, name="observation_id")
        _require_surface(self.surface)
        _require_text(self.request_key, name="request_key")
        captured_at = _canonical_instant(self.captured_at, name="captured_at")
        object.__setattr__(self, "captured_at", captured_at)
        _require_uuid7(self.source_session_id, name="source_session_id")
        _non_negative_int(self.source_sequence, name="source_sequence")
        _require_text(self.evidence_ref, name="evidence_ref")
        _require_sha256(self.response_text_sha256, name="response_text_sha256")
        _require_sha256(self.artifact_sha256, name="artifact_sha256")
        _require_text(self.payload, name="payload")


class _ImmediateTransaction:
    """One explicit SQLite transaction; no deferred generator cleanup."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market store transaction failed"
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc_type, tb
        try:
            if exc is None:
                self._connection.execute("COMMIT")
            elif self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except sqlite3.Error as transaction_error:
            if self._connection.in_transaction:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise MarketStoreOperationError(
                "market store transaction failed"
            ) from transaction_error

        if isinstance(exc, sqlite3.Error):
            raise MarketStoreOperationError(
                "market store transaction failed"
            ) from exc
        return False


class MarketStore:
    """Append-only market analytics observation SQLite store."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        *,
        read_only: bool,
    ) -> None:
        self.path = path
        self._connection = connection
        self._read_only = read_only
        self._closed = False

    @classmethod
    def open_rw(cls, path: Path) -> "MarketStore":
        resolved = Path(path).expanduser().resolve(strict=False)
        connection = _connect_rw(resolved)
        store = cls(resolved, connection, read_only=False)
        try:
            store._bootstrap_or_validate()
        except BaseException:
            connection.close()
            raise
        return store

    @classmethod
    def open_read_only_if_exists(cls, path: Path) -> "MarketStore | None":
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.exists():
            return None
        if not resolved.is_file():
            raise MarketStoreCompatibilityError(
                "market store database path is not a regular file"
            )
        connection = _connect_ro(resolved)
        store = cls(resolved, connection, read_only=True)
        try:
            store._validate_existing()
        except BaseException:
            connection.close()
            raise
        return store

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._connection.close()
        finally:
            self._closed = True

    def __enter__(self) -> "MarketStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _bootstrap_or_validate(self) -> None:
        application_id = int(
            self._connection.execute("PRAGMA application_id").fetchone()[0]
        )
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        tables = _user_tables(self._connection)

        if application_id == 0 and user_version == 0 and not tables:
            self._initialize_schema()
        self._validate_existing()

    def _initialize_schema(self) -> None:
        with _ImmediateTransaction(self._connection):
            for statement in _SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self._connection.execute(f"PRAGMA user_version = {USER_VERSION}")

    def _validate_schema_identity(self) -> None:
        mode = str(
            self._connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).casefold()
        if mode != "wal":
            raise MarketStoreCompatibilityError(
                "market store requires SQLite WAL journal mode"
            )
        application_id = int(
            self._connection.execute("PRAGMA application_id").fetchone()[0]
        )
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if application_id != APPLICATION_ID:
            raise MarketStoreCompatibilityError(
                f"foreign market store database application_id={application_id}"
            )
        if user_version != USER_VERSION:
            relation = "newer" if user_version > USER_VERSION else "older"
            raise MarketStoreCompatibilityError(
                f"{relation} market store schema {user_version} is unsupported; "
                f"expected {USER_VERSION}"
            )

        tables = _user_tables(self._connection)
        if tables != _EXPECTED_TABLES:
            raise MarketStoreCompatibilityError(
                "market store database user tables do not match schema v1"
            )
        strict = {
            str(row[1]): int(row[5])
            for row in self._connection.execute("PRAGMA table_list")
            if str(row[1]) in _EXPECTED_TABLES
        }
        if set(strict) != _EXPECTED_TABLES:
            raise MarketStoreCompatibilityError(
                "market store database is missing required schema tables"
            )
        if any(value != 1 for value in strict.values()):
            raise MarketStoreCompatibilityError(
                "market store database tables must all be STRICT"
            )
        if not _REQUIRED_TRIGGERS <= _trigger_names(self._connection):
            raise MarketStoreCompatibilityError(
                "market store database is missing append-only triggers"
            )

    def verify_storage_integrity(self) -> None:
        """Run SQLite physical, foreign-key and append-only checks."""

        try:
            integrity = [
                str(row[0])
                for row in self._connection.execute("PRAGMA integrity_check")
            ]
            foreign_keys = tuple(
                self._connection.execute("PRAGMA foreign_key_check")
            )
            triggers = _trigger_names(self._connection)
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market store integrity verification failed"
            ) from exc
        if integrity != ["ok"]:
            raise MarketStoreIntegrityError(
                "market store storage integrity check failed"
            )
        if foreign_keys:
            raise MarketStoreIntegrityError(
                "market store contains foreign-key violations"
            )
        if not _REQUIRED_TRIGGERS <= triggers:
            raise MarketStoreIntegrityError(
                "market store is missing append-only triggers"
            )

    def _validate_existing(self) -> None:
        try:
            self._validate_schema_identity()
            self.verify_storage_integrity()
        except MarketStoreError:
            raise
        except (TypeError, ValueError) as exc:
            raise MarketStoreIntegrityError(
                "market store persisted values violate semantic invariants"
            ) from exc
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market store validation failed"
            ) from exc

    def _rollback_quietly(self) -> None:
        if self._connection.in_transaction:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def append(self, record: MarketObservationRecord) -> None:
        if not isinstance(record, MarketObservationRecord):
            raise TypeError("record must be MarketObservationRecord")
        if self._read_only:
            raise MarketStoreCompatibilityError(
                "market store database is open read-only"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market store transaction failed"
            ) from exc
        try:
            self._connection.execute(
                """
                INSERT INTO observation(
                    observation_id, surface, request_key, captured_at,
                    source_session_id, source_sequence, evidence_ref,
                    response_text_sha256, artifact_sha256, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.observation_id,
                    record.surface,
                    record.request_key,
                    record.captured_at,
                    record.source_session_id,
                    record.source_sequence,
                    record.evidence_ref,
                    record.response_text_sha256,
                    record.artifact_sha256,
                    record.payload,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._rollback_quietly()
            raise MarketStoreConflictError(
                "market observation conflicts with stored content"
            ) from exc
        except sqlite3.Error as exc:
            self._rollback_quietly()
            raise MarketStoreOperationError(
                "market observation append failed"
            ) from exc

    def observations(
        self,
        *,
        surface: str | None = None,
        request_key: str | None = None,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> tuple[MarketObservationRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1 or limit > MAX_READ_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_READ_LIMIT}")
        if surface is not None:
            _require_text(surface, name="surface")
        if request_key is not None:
            _require_text(request_key, name="request_key")

        conditions: list[str] = []
        parameters: list[object] = []
        if surface is not None:
            conditions.append("surface = ?")
            parameters.append(surface)
        if request_key is not None:
            conditions.append("request_key = ?")
            parameters.append(request_key)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)

        try:
            rows = tuple(
                self._connection.execute(
                    f"""
                    SELECT observation_id, surface, request_key, captured_at,
                           source_session_id, source_sequence, evidence_ref,
                           response_text_sha256, artifact_sha256, payload
                    FROM observation{where}
                    ORDER BY captured_at, source_sequence, observation_id
                    LIMIT ?
                    """,
                    parameters,
                )
            )
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market observation read failed"
            ) from exc

        try:
            return tuple(
                MarketObservationRecord(
                    observation_id=str(row[0]),
                    surface=str(row[1]),
                    request_key=str(row[2]),
                    captured_at=str(row[3]),
                    source_session_id=str(row[4]),
                    source_sequence=int(row[5]),
                    evidence_ref=str(row[6]),
                    response_text_sha256=str(row[7]),
                    artifact_sha256=str(row[8]),
                    payload=str(row[9]),
                )
                for row in rows
            )
        except (TypeError, ValueError) as exc:
            raise MarketStoreIntegrityError(
                "persisted market observation violates semantic invariants"
            ) from exc

    def count(self) -> int:
        try:
            row = self._connection.execute(
                "SELECT count(*) FROM observation"
            ).fetchone()
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market observation count failed"
            ) from exc
        return int(row[0])

    def projection_fingerprint(self) -> str:
        try:
            observations = [
                [str(value) for value in row]
                for row in self._connection.execute(
                    """
                    SELECT surface, request_key, captured_at, observation_id,
                           artifact_sha256, response_text_sha256, evidence_ref
                    FROM observation
                    ORDER BY surface, request_key, captured_at, observation_id,
                             artifact_sha256, response_text_sha256, evidence_ref
                    """
                )
            ]
        except sqlite3.Error as exc:
            raise MarketStoreOperationError(
                "market projection fingerprint failed"
            ) from exc
        return canonical_sha256(
            {
                "schema": "bizman.market-projection.v1",
                "observations": observations,
            }
        )


__all__ = [
    "APPLICATION_ID",
    "DEFAULT_READ_LIMIT",
    "MAX_READ_LIMIT",
    "MIN_SQLITE_VERSION",
    "SURFACES",
    "USER_VERSION",
    "MarketObservationRecord",
    "MarketStore",
    "MarketStoreCompatibilityError",
    "MarketStoreConflictError",
    "MarketStoreError",
    "MarketStoreIntegrityError",
    "MarketStoreOperationError",
]
