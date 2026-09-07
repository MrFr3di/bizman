from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Final

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import AnalysisProfile, Finding
from tools.bizman_foundation.fingerprint import canonical_json_bytes


APPLICATION_ID: Final[int] = 0x424D4431
USER_VERSION: Final[int] = 1
_MIN_SQLITE_VERSION: Final[tuple[int, int, int]] = (3, 37, 0)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHANGE_QUERY_CHUNK = 400


class StateError(RuntimeError):
    """Base class for detector operational-state failures."""


class StateCompatibilityError(StateError):
    """The SQLite file/runtime is not compatible with detector schema v1."""


class StateIntegrityError(StateError):
    """Persisted detector state conflicts with immutable semantic identity."""


@dataclass(frozen=True, slots=True)
class OutboxPayload:
    bundle_id: str
    payload_sha256: str
    payload_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class OutboxItem:
    bundle_id: str
    analysis_profile_sha256: str
    baseline_sha256: str
    session_id: str
    payload_sha256: str
    payload_json: str
    state: str
    created_at: str
    materialized_at: str | None


@dataclass(frozen=True, slots=True)
class TransactionResult:
    processed: bool
    first_seen_change_ids: tuple[str, ...] = ()
    outbox_bundle_id: str | None = None


OutboxFactory = Callable[
    [EvidenceIdentity, AnalysisProfile, tuple[Finding, ...]], OutboxPayload | None
]


_SCHEMA = (
    """
    CREATE TABLE processed_sessions (
        session_id TEXT NOT NULL,
        analysis_profile_sha256 TEXT NOT NULL,
        baseline_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        PRIMARY KEY (session_id, analysis_profile_sha256)
    ) STRICT
    """,
    """
    CREATE TABLE changes (
        analysis_profile_sha256 TEXT NOT NULL,
        change_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        rule_version INTEGER NOT NULL CHECK (rule_version > 0),
        kind TEXT NOT NULL,
        novelty_class TEXT NOT NULL,
        identity_json TEXT NOT NULL,
        first_session_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_session_id TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
        PRIMARY KEY (analysis_profile_sha256, change_id)
    ) STRICT
    """,
    """
    CREATE TABLE promotion_outbox (
        bundle_id TEXT PRIMARY KEY,
        analysis_profile_sha256 TEXT NOT NULL,
        baseline_sha256 TEXT NOT NULL,
        session_id TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending','materialized')),
        created_at TEXT NOT NULL,
        materialized_at TEXT
    ) STRICT
    """,
    """
    CREATE INDEX promotion_outbox_pending_idx
    ON promotion_outbox(state, created_at, bundle_id)
    """,
)

_EXPECTED_TABLES = frozenset({"processed_sessions", "changes", "promotion_outbox"})


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise StateIntegrityError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise StateIntegrityError(f"{name} must be a non-empty string")
    return value


def _connect_rw(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise StateCompatibilityError(
            "detector state requires SQLite >= 3.37.0 for STRICT tables"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
        connection = sqlite3.connect(path, timeout=5.0, autocommit=True)
    else:  # Python 3.11 compatibility.
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).casefold()
    if mode != "wal":
        connection.close()
        raise StateCompatibilityError("detector state requires SQLite WAL journal mode")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def _connect_ro(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise StateCompatibilityError(
            "detector state requires SQLite >= 3.37.0 for STRICT tables"
        )
    uri = f"{path.resolve().as_uri()}?mode=ro"
    if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
        connection = sqlite3.connect(uri, timeout=5.0, uri=True, autocommit=True)
    else:
        connection = sqlite3.connect(uri, timeout=5.0, uri=True, isolation_level=None)
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


class DetectorState:
    """Rebuildable SQLite detector state with explicit transactional outbox."""

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
    def open_rw(cls, path: Path) -> "DetectorState":
        resolved = Path(path).expanduser().resolve(strict=False)
        connection = _connect_rw(resolved)
        state = cls(resolved, connection, read_only=False)
        try:
            state._bootstrap_or_validate()
        except BaseException:
            connection.close()
            raise
        return state

    @classmethod
    def open_read_only_if_exists(cls, path: Path) -> "DetectorState | None":
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.exists():
            return None
        if not resolved.is_file():
            raise StateCompatibilityError(f"detector state path is not a file: {resolved}")
        connection = _connect_ro(resolved)
        state = cls(resolved, connection, read_only=True)
        try:
            state._validate_existing()
        except BaseException:
            connection.close()
            raise
        return state

    def close(self) -> None:
        if self._closed:
            return
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")
        self._connection.close()
        self._closed = True

    def __enter__(self) -> "DetectorState":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _user_tables(self) -> set[str]:
        return {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _bootstrap_or_validate(self) -> None:
        application_id = int(self._connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        tables = self._user_tables()

        if application_id == 0 and user_version == 0 and not tables:
            self._initialize_schema()
            self._validate_existing()
            return
        if application_id not in {0, APPLICATION_ID}:
            raise StateCompatibilityError(
                f"foreign detector database application_id={application_id}"
            )
        if application_id == 0:
            raise StateCompatibilityError(
                "unidentified non-empty SQLite database cannot be adopted as detector state"
            )
        if user_version > USER_VERSION:
            raise StateCompatibilityError(
                f"detector database schema {user_version} is newer than supported {USER_VERSION}"
            )
        if user_version < USER_VERSION:
            raise StateCompatibilityError(
                f"detector database schema {user_version} requires an explicit migration"
            )
        self._validate_existing()

    def _initialize_schema(self) -> None:
        with self._immediate_transaction():
            for statement in _SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self._connection.execute(f"PRAGMA user_version = {USER_VERSION}")

    def _validate_existing(self) -> None:
        application_id = int(self._connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id != APPLICATION_ID:
            raise StateCompatibilityError(
                f"foreign detector database application_id={application_id}"
            )
        if user_version > USER_VERSION:
            raise StateCompatibilityError(
                f"detector database schema {user_version} is newer than supported {USER_VERSION}"
            )
        if user_version != USER_VERSION:
            raise StateCompatibilityError(
                f"unsupported detector database schema version {user_version}"
            )

        table_rows = {
            str(row[1]): int(row[5])
            for row in self._connection.execute("PRAGMA table_list")
            if str(row[1]) in _EXPECTED_TABLES
        }
        if set(table_rows) != _EXPECTED_TABLES:
            raise StateCompatibilityError("detector database is missing required schema tables")
        if any(strict != 1 for strict in table_rows.values()):
            raise StateCompatibilityError("detector database tables must all be STRICT")

    def existing_change_ids(
        self,
        analysis_profile_sha256: str,
        change_ids: Iterable[str],
    ) -> frozenset[str]:
        """Return exactly the requested changes already present in one profile."""

        profile_sha256 = _require_sha256(
            analysis_profile_sha256,
            name="analysis_profile_sha256",
        )
        if isinstance(change_ids, (str, bytes)):
            raise TypeError("change_ids must be an iterable of change-id strings")
        ordered = tuple(
            sorted({_require_text(change_id, name="change_id") for change_id in change_ids})
        )
        if not ordered:
            return frozenset()

        result: set[str] = set()
        for offset in range(0, len(ordered), _CHANGE_QUERY_CHUNK):
            chunk = ordered[offset : offset + _CHANGE_QUERY_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                f"SELECT change_id FROM changes "
                f"WHERE analysis_profile_sha256 = ? AND change_id IN ({placeholders})",
                (profile_sha256, *chunk),
            ).fetchall()
            result.update(str(row[0]) for row in rows)
        return frozenset(result)

    @contextmanager
    def _immediate_transaction(self) -> Iterator[None]:
        if self._read_only:
            raise StateCompatibilityError("detector state is open read-only")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _finding_identity_json(finding: Finding) -> str:
        value = {
            "change_id": finding.change_id,
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "kind": finding.kind,
            "novelty_class": finding.novelty_class,
            "subject": finding.subject,
            "delta": finding.delta,
        }
        return canonical_json_bytes(value).decode("utf-8")

    @staticmethod
    def _validate_outbox_payload(payload: OutboxPayload) -> OutboxPayload:
        if not isinstance(payload, OutboxPayload):
            raise StateIntegrityError("outbox factory must return OutboxPayload or None")
        _require_text(payload.bundle_id, name="bundle_id")
        _require_sha256(payload.payload_sha256, name="payload_sha256")
        _require_text(payload.payload_json, name="payload_json")
        _require_text(payload.created_at, name="created_at")
        actual = hashlib.sha256(payload.payload_json.encode("utf-8")).hexdigest()
        if actual != payload.payload_sha256:
            raise StateIntegrityError(
                f"outbox payload hash mismatch for {payload.bundle_id!r}: {actual}"
            )
        try:
            json.loads(payload.payload_json)
        except json.JSONDecodeError as exc:
            raise StateIntegrityError("outbox payload_json must contain valid JSON") from exc
        return payload

    def process_session_transaction(
        self,
        *,
        identity: EvidenceIdentity,
        profile: AnalysisProfile,
        findings: tuple[Finding, ...],
        outbox_factory: OutboxFactory,
        processed_at: str,
    ) -> TransactionResult:
        if not isinstance(identity, EvidenceIdentity):
            raise TypeError("identity must be EvidenceIdentity")
        if not isinstance(profile, AnalysisProfile):
            raise TypeError("profile must be AnalysisProfile")
        if not callable(outbox_factory):
            raise TypeError("outbox_factory must be callable")
        _require_text(processed_at, name="processed_at")
        _require_sha256(identity.manifest_sha256, name="manifest_sha256")
        _require_sha256(identity.evidence_sha256, name="evidence_sha256")
        _require_sha256(profile.sha256, name="analysis_profile_sha256")
        _require_sha256(profile.baseline_sha256, name="baseline_sha256")

        ordered_findings = tuple(sorted(findings, key=lambda item: item.change_id))
        seen_change_ids: set[str] = set()
        prepared: list[tuple[Finding, str, bool]] = []

        with self._immediate_transaction():
            evidence_rows = self._connection.execute(
                """
                SELECT DISTINCT evidence_sha256, manifest_sha256
                FROM processed_sessions
                WHERE session_id = ?
                """,
                (identity.session_id,),
            ).fetchall()
            expected_evidence = (identity.evidence_sha256, identity.manifest_sha256)
            if evidence_rows and (
                len(evidence_rows) != 1 or tuple(evidence_rows[0]) != expected_evidence
            ):
                raise StateIntegrityError(
                    "session evidence identity conflicts across analysis profiles"
                )

            checkpoint = self._connection.execute(
                """
                SELECT baseline_sha256, evidence_sha256, manifest_sha256
                FROM processed_sessions
                WHERE session_id = ? AND analysis_profile_sha256 = ?
                """,
                (identity.session_id, profile.sha256),
            ).fetchone()
            if checkpoint is not None:
                expected = (
                    profile.baseline_sha256,
                    identity.evidence_sha256,
                    identity.manifest_sha256,
                )
                if tuple(checkpoint) != expected:
                    raise StateIntegrityError(
                        "processed session/profile checkpoint conflicts with immutable evidence"
                    )
                return TransactionResult(processed=False)

            first_seen: list[Finding] = []
            for finding in ordered_findings:
                if not isinstance(finding, Finding):
                    raise TypeError("findings must contain Finding values")
                if finding.change_id in seen_change_ids:
                    raise StateIntegrityError(
                        f"duplicate finding change_id in one transaction: {finding.change_id}"
                    )
                seen_change_ids.add(finding.change_id)
                if finding.rule_version <= 0:
                    raise StateIntegrityError("finding rule_version must be positive")
                identity_json = self._finding_identity_json(finding)
                existing = self._connection.execute(
                    """
                    SELECT rule_id, rule_version, kind, novelty_class, identity_json
                    FROM changes
                    WHERE analysis_profile_sha256 = ? AND change_id = ?
                    """,
                    (profile.sha256, finding.change_id),
                ).fetchone()
                is_first = existing is None
                if existing is not None:
                    expected = (
                        finding.rule_id,
                        finding.rule_version,
                        finding.kind,
                        finding.novelty_class,
                        identity_json,
                    )
                    if tuple(existing) != expected:
                        raise StateIntegrityError(
                            f"change identity conflict for {finding.change_id}"
                        )
                else:
                    first_seen.append(finding)
                prepared.append((finding, identity_json, is_first))

            first_seen_tuple = tuple(first_seen)
            payload = (
                outbox_factory(identity, profile, first_seen_tuple)
                if first_seen_tuple
                else None
            )
            if payload is not None:
                payload = self._validate_outbox_payload(payload)

            for finding, identity_json, is_first in prepared:
                if is_first:
                    self._connection.execute(
                        """
                        INSERT INTO changes (
                            analysis_profile_sha256, change_id, rule_id, rule_version,
                            kind, novelty_class, identity_json, first_session_id,
                            first_seen_at, last_session_id, last_seen_at, occurrence_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            profile.sha256,
                            finding.change_id,
                            finding.rule_id,
                            finding.rule_version,
                            finding.kind,
                            finding.novelty_class,
                            identity_json,
                            identity.session_id,
                            identity.ended_at,
                            identity.session_id,
                            identity.ended_at,
                        ),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE changes
                        SET last_session_id = ?, last_seen_at = ?,
                            occurrence_count = occurrence_count + 1
                        WHERE analysis_profile_sha256 = ? AND change_id = ?
                        """,
                        (
                            identity.session_id,
                            identity.ended_at,
                            profile.sha256,
                            finding.change_id,
                        ),
                    )

            self._connection.execute(
                """
                INSERT INTO processed_sessions (
                    session_id, analysis_profile_sha256, baseline_sha256,
                    evidence_sha256, manifest_sha256, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.session_id,
                    profile.sha256,
                    profile.baseline_sha256,
                    identity.evidence_sha256,
                    identity.manifest_sha256,
                    processed_at,
                ),
            )

            outbox_bundle_id: str | None = None
            if payload is not None:
                try:
                    self._connection.execute(
                        """
                        INSERT INTO promotion_outbox (
                            bundle_id, analysis_profile_sha256, baseline_sha256,
                            session_id, payload_sha256, payload_json, state,
                            created_at, materialized_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
                        """,
                        (
                            payload.bundle_id,
                            profile.sha256,
                            profile.baseline_sha256,
                            identity.session_id,
                            payload.payload_sha256,
                            payload.payload_json,
                            payload.created_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise StateIntegrityError(
                        f"outbox bundle identity collision: {payload.bundle_id}"
                    ) from exc
                outbox_bundle_id = payload.bundle_id

            return TransactionResult(
                processed=True,
                first_seen_change_ids=tuple(item.change_id for item in first_seen_tuple),
                outbox_bundle_id=outbox_bundle_id,
            )

    def pending_outbox(self) -> tuple[OutboxItem, ...]:
        rows = self._connection.execute(
            """
            SELECT bundle_id, analysis_profile_sha256, baseline_sha256, session_id,
                   payload_sha256, payload_json, state, created_at, materialized_at
            FROM promotion_outbox
            WHERE state = 'pending'
            ORDER BY created_at, bundle_id
            """
        ).fetchall()
        return tuple(OutboxItem(*row) for row in rows)

    def mark_materialized(
        self,
        bundle_id: str,
        payload_sha256: str,
        at: str,
    ) -> None:
        _require_text(bundle_id, name="bundle_id")
        _require_sha256(payload_sha256, name="payload_sha256")
        _require_text(at, name="materialized_at")
        with self._immediate_transaction():
            row = self._connection.execute(
                "SELECT payload_sha256, state FROM promotion_outbox WHERE bundle_id = ?",
                (bundle_id,),
            ).fetchone()
            if row is None:
                raise StateIntegrityError(f"unknown outbox bundle {bundle_id!r}")
            stored_hash, state = str(row[0]), str(row[1])
            if stored_hash != payload_sha256:
                raise StateIntegrityError(
                    f"outbox payload hash mismatch for {bundle_id!r}"
                )
            if state == "materialized":
                return
            if state != "pending":
                raise StateIntegrityError(
                    f"invalid outbox state {state!r} for {bundle_id!r}"
                )
            self._connection.execute(
                """
                UPDATE promotion_outbox
                SET state = 'materialized', materialized_at = ?
                WHERE bundle_id = ?
                """,
                (at, bundle_id),
            )


__all__ = [
    "APPLICATION_ID",
    "USER_VERSION",
    "DetectorState",
    "OutboxFactory",
    "OutboxItem",
    "OutboxPayload",
    "StateCompatibilityError",
    "StateError",
    "StateIntegrityError",
    "TransactionResult",
]
