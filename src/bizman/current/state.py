from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Final, Iterator

from bizman.current.model import (
    CurrentProjectionSpec,
    CurrentStateSnapshot,
    ReplaySession,
    build_current_snapshot,
)


APPLICATION_ID: Final[int] = 0x424D4331
USER_VERSION: Final[int] = 1
_MIN_SQLITE_VERSION: Final[tuple[int, int, int]] = (3, 37, 0)
_EXPECTED_TABLES = frozenset({"projection_meta", "replayed_session"})


class CurrentStateError(RuntimeError):
    """Base class for Current State storage failures."""


class CurrentStateCompatibilityError(CurrentStateError):
    """The SQLite file/runtime is incompatible with Current State schema."""


class CurrentStateIntegrityError(CurrentStateError):
    """Persisted Current State violates deterministic semantic identity."""


class CurrentStateOperationError(CurrentStateError):
    """A Current State SQLite operation failed."""


_SCHEMA = (
    """
    CREATE TABLE projection_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        projection_name TEXT NOT NULL,
        projection_version INTEGER NOT NULL CHECK (projection_version > 0),
        analysis_profile_sha256 TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL,
        state_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ready', 'stale')),
        stale_reason TEXT,
        session_count INTEGER NOT NULL CHECK (session_count >= 0),
        last_session_id TEXT,
        last_sequence INTEGER CHECK (last_sequence IS NULL OR last_sequence >= 0),
        CHECK (
            (status = 'ready' AND stale_reason IS NULL)
            OR
            (status = 'stale' AND stale_reason IS NOT NULL AND length(stale_reason) > 0)
        ),
        CHECK (
            (session_count = 0 AND last_session_id IS NULL AND last_sequence IS NULL)
            OR
            (session_count > 0 AND last_session_id IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE replayed_session (
        session_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
        event_count INTEGER NOT NULL CHECK (event_count >= 0),
        last_sequence INTEGER CHECK (last_sequence IS NULL OR last_sequence >= 0),
        CHECK (
            (event_count = 0 AND last_sequence IS NULL)
            OR
            (event_count > 0 AND last_sequence = event_count - 1)
        )
    ) STRICT
    """,
    """
    CREATE INDEX replayed_session_order_idx
    ON replayed_session(started_at, session_id)
    """,
)


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _preflight_rw_identity(connection: sqlite3.Connection) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables = _user_tables(connection)

    if application_id == 0:
        if user_version == 0 and not tables:
            return
        raise CurrentStateCompatibilityError(
            "unidentified non-empty SQLite database cannot be adopted as Current State"
        )
    if application_id != APPLICATION_ID:
        raise CurrentStateCompatibilityError(
            f"foreign Current State database application_id={application_id}"
        )
    if user_version != USER_VERSION:
        relation = "newer" if user_version > USER_VERSION else "older"
        raise CurrentStateCompatibilityError(
            f"{relation} Current State schema {user_version} is unsupported; "
            f"expected {USER_VERSION}"
        )


def _connect_rw(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise CurrentStateCompatibilityError(
            "Current State requires SQLite >= 3.37.0 for STRICT tables"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
            connection = sqlite3.connect(path, timeout=5.0, autocommit=True)
        else:  # Python 3.11 compatibility.
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
                raise CurrentStateCompatibilityError(
                    "Current State requires SQLite WAL journal mode"
                )
            connection.execute("PRAGMA synchronous = NORMAL")
            return connection
        except BaseException:
            connection.close()
            raise
    except CurrentStateError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise CurrentStateOperationError(
            "cannot open Current State database for writing"
        ) from exc


def _connect_ro(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise CurrentStateCompatibilityError(
            "Current State requires SQLite >= 3.37.0 for STRICT tables"
        )
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
            connection = sqlite3.connect(
                uri,
                timeout=5.0,
                uri=True,
                autocommit=True,
            )
        else:
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
        raise CurrentStateOperationError(
            "cannot open Current State database read-only"
        ) from exc


class CurrentStateStore:
    """Rebuildable deterministic Current State SQLite store."""

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
    def open_rw(cls, path: Path) -> "CurrentStateStore":
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
    def open_read_only_if_exists(
        cls,
        path: Path,
    ) -> "CurrentStateStore | None":
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.exists():
            return None
        if not resolved.is_file():
            raise CurrentStateCompatibilityError(
                "Current State database path is not a regular file"
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

    def __enter__(self) -> "CurrentStateStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @contextmanager
    def _immediate_transaction(self) -> Iterator[None]:
        if self._read_only:
            raise CurrentStateCompatibilityError(
                "Current State database is open read-only"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute("COMMIT")
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        except CurrentStateError:
            raise
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise CurrentStateOperationError(
                "Current State transaction failed"
            ) from exc

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
        with self._immediate_transaction():
            for statement in _SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(
                f"PRAGMA application_id = {APPLICATION_ID}"
            )
            self._connection.execute(f"PRAGMA user_version = {USER_VERSION}")

    def _validate_existing(self) -> None:
        try:
            application_id = int(
                self._connection.execute("PRAGMA application_id").fetchone()[0]
            )
            user_version = int(
                self._connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if application_id != APPLICATION_ID:
                raise CurrentStateCompatibilityError(
                    f"foreign Current State database application_id={application_id}"
                )
            if user_version != USER_VERSION:
                relation = "newer" if user_version > USER_VERSION else "older"
                raise CurrentStateCompatibilityError(
                    f"{relation} Current State schema {user_version} is unsupported; "
                    f"expected {USER_VERSION}"
                )

            strict = {
                str(row[1]): int(row[5])
                for row in self._connection.execute("PRAGMA table_list")
                if str(row[1]) in _EXPECTED_TABLES
            }
            if set(strict) != _EXPECTED_TABLES:
                raise CurrentStateCompatibilityError(
                    "Current State database is missing required schema tables"
                )
            if any(value != 1 for value in strict.values()):
                raise CurrentStateCompatibilityError(
                    "Current State database tables must all be STRICT"
                )

            meta_count = int(
                self._connection.execute(
                    "SELECT count(*) FROM projection_meta"
                ).fetchone()[0]
            )
            ledger_count = int(
                self._connection.execute(
                    "SELECT count(*) FROM replayed_session"
                ).fetchone()[0]
            )
            if meta_count not in {0, 1}:
                raise CurrentStateIntegrityError(
                    "Current State must contain at most one metadata row"
                )
            if meta_count == 0 and ledger_count != 0:
                raise CurrentStateIntegrityError(
                    "Current State replay ledger exists without metadata"
                )
            if meta_count == 1:
                self._snapshot_unchecked()
        except CurrentStateError:
            raise
        except (TypeError, ValueError) as exc:
            raise CurrentStateIntegrityError(
                "Current State persisted values violate semantic invariants"
            ) from exc
        except sqlite3.Error as exc:
            raise CurrentStateOperationError(
                "Current State validation failed"
            ) from exc

    def _snapshot_unchecked(self) -> CurrentStateSnapshot | None:
        meta = self._connection.execute(
            """
            SELECT projection_name, projection_version,
                   analysis_profile_sha256, input_fingerprint,
                   state_fingerprint, status, stale_reason,
                   session_count, last_session_id, last_sequence
            FROM projection_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if meta is None:
            return None

        sessions = tuple(
            ReplaySession(
                session_id=str(row[0]),
                manifest_sha256=str(row[1]),
                evidence_sha256=str(row[2]),
                started_at=str(row[3]),
                ended_at=str(row[4]),
                status=str(row[5]),
                event_count=int(row[6]),
                last_sequence=(
                    int(row[7]) if row[7] is not None else None
                ),
            )
            for row in self._connection.execute(
                """
                SELECT session_id, manifest_sha256, evidence_sha256,
                       started_at, ended_at, status,
                       event_count, last_sequence
                FROM replayed_session
                ORDER BY started_at, session_id
                """
            )
        )

        from bizman.current.model import CurrentStateMetadata

        metadata = CurrentStateMetadata(
            projection_name=str(meta[0]),
            projection_version=int(meta[1]),
            analysis_profile_sha256=str(meta[2]),
            input_fingerprint=str(meta[3]),
            state_fingerprint=str(meta[4]),
            status=str(meta[5]),
            stale_reason=(
                str(meta[6]) if meta[6] is not None else None
            ),
            session_count=int(meta[7]),
            last_session_id=(
                str(meta[8]) if meta[8] is not None else None
            ),
            last_sequence=(
                int(meta[9]) if meta[9] is not None else None
            ),
        )
        return CurrentStateSnapshot(
            metadata=metadata,
            sessions=sessions,
        )

    def snapshot(self) -> CurrentStateSnapshot | None:
        try:
            value = self._snapshot_unchecked()
        except CurrentStateError:
            raise
        except (TypeError, ValueError) as exc:
            raise CurrentStateIntegrityError(
                "Current State persisted fingerprint or ledger is invalid"
            ) from exc
        except sqlite3.Error as exc:
            raise CurrentStateOperationError(
                "Current State snapshot read failed"
            ) from exc
        return value

    def replace_snapshot(self, snapshot: CurrentStateSnapshot) -> None:
        if not isinstance(snapshot, CurrentStateSnapshot):
            raise TypeError("snapshot must be CurrentStateSnapshot")

        meta = snapshot.metadata
        with self._immediate_transaction():
            self._connection.execute("DELETE FROM replayed_session")
            self._connection.execute("DELETE FROM projection_meta")
            self._connection.executemany(
                """
                INSERT INTO replayed_session(
                    session_id, manifest_sha256, evidence_sha256,
                    started_at, ended_at, status,
                    event_count, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.session_id,
                        item.manifest_sha256,
                        item.evidence_sha256,
                        item.started_at,
                        item.ended_at,
                        item.status,
                        item.event_count,
                        item.last_sequence,
                    )
                    for item in snapshot.sessions
                ),
            )
            self._connection.execute(
                """
                INSERT INTO projection_meta(
                    singleton, projection_name, projection_version,
                    analysis_profile_sha256, input_fingerprint,
                    state_fingerprint, status, stale_reason,
                    session_count, last_session_id, last_sequence
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.projection_name,
                    meta.projection_version,
                    meta.analysis_profile_sha256,
                    meta.input_fingerprint,
                    meta.state_fingerprint,
                    meta.status,
                    meta.stale_reason,
                    meta.session_count,
                    meta.last_session_id,
                    meta.last_sequence,
                ),
            )

        persisted = self.snapshot()
        if persisted != snapshot:
            raise CurrentStateIntegrityError(
                "persisted Current State does not match requested snapshot"
            )

    def mark_stale(self, reason: str) -> CurrentStateSnapshot:
        if not isinstance(reason, str) or not reason:
            raise ValueError("stale reason must be a non-empty string")
        current = self.snapshot()
        if current is None:
            raise CurrentStateIntegrityError(
                "cannot mark an unbuilt Current State stale"
            )
        spec = CurrentProjectionSpec(
            projection_name=current.metadata.projection_name,
            projection_version=current.metadata.projection_version,
            analysis_profile_sha256=current.metadata.analysis_profile_sha256,
        )
        stale = build_current_snapshot(
            spec,
            current.sessions,
            status="stale",
            stale_reason=reason,
        )
        self.replace_snapshot(stale)
        return stale


__all__ = [
    "APPLICATION_ID",
    "USER_VERSION",
    "CurrentStateCompatibilityError",
    "CurrentStateError",
    "CurrentStateIntegrityError",
    "CurrentStateOperationError",
    "CurrentStateStore",
]
