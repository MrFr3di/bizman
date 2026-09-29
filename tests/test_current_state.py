from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.current import (
    APPLICATION_ID,
    PROJECTION_NAME,
    PROJECTION_VERSION,
    USER_VERSION,
    CurrentProjectionSpec,
    CurrentStateCompatibilityError,
    CurrentStateIntegrityError,
    CurrentStateSnapshot,
    CurrentStateStore,
    ReplaySession,
    build_current_snapshot,
    build_replay_snapshot,
    rebuild_current_state,
)
from bizman.foundation.redaction import load_redaction_policy


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000011"
SESSION_B = "01991c7d-a400-7000-8000-000000000012"
SESSION_RUNNING = "01991c7d-a400-7000-8000-000000000013"
SESSION_FAILED = "01991c7d-a400-7000-8000-000000000014"
EVENT_A0 = "01991c7d-a400-7000-8000-000000000101"
EVENT_A1 = "01991c7d-a400-7000-8000-000000000102"
EVENT_B0 = "01991c7d-a400-7000-8000-000000000103"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _manifest(
    session_id: str,
    *,
    started_at: str,
    ended_at: str | None,
    status: str,
    event_files: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
        "collector": {"name": "bizman-cdp", "version": "0.test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": event_files,
        "artifact_count": 0,
        "warnings": [],
    }


def _event(
    session_id: str,
    sequence: int,
    event_id: str,
    *,
    event_type: str = "fixture.event",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": "system",
        "event_type": event_type,
        "confidence": "observed",
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for event in events
        ),
        encoding="utf-8",
    )


def _write_session(
    data_dir: Path,
    *,
    session_id: str,
    started_at: str,
    ended_at: str | None,
    status: str,
    events: list[dict[str, object]],
) -> Path:
    event_rel = f"events/2026-09-07/{session_id}.jsonl"
    event_path = data_dir / event_rel
    _write_events(event_path, events)
    _write_json(
        data_dir / "sessions" / session_id / "manifest.json",
        _manifest(
            session_id,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            event_files=[event_rel],
        ),
    )
    return event_path


def _write_replay_fixture(data_dir: Path) -> dict[str, Path]:
    # Deliberately make SESSION_B earlier than SESSION_A to prove chronological
    # ordering rather than directory/session-id ordering.
    a = _write_session(
        data_dir,
        session_id=SESSION_A,
        started_at="2026-09-07T12:02:00Z",
        ended_at="2026-09-07T12:03:00Z",
        status="completed",
        events=[
            _event(SESSION_A, 0, EVENT_A0),
            _event(SESSION_A, 1, EVENT_A1),
        ],
    )
    b = _write_session(
        data_dir,
        session_id=SESSION_B,
        started_at="2026-09-07T12:00:00+00:00",
        ended_at="2026-09-07T12:01:00+00:00",
        status="cancelled",
        events=[_event(SESSION_B, 0, EVENT_B0)],
    )
    _write_session(
        data_dir,
        session_id=SESSION_RUNNING,
        started_at="2026-09-07T12:04:00Z",
        ended_at=None,
        status="running",
        events=[],
    )
    _write_session(
        data_dir,
        session_id=SESSION_FAILED,
        started_at="2026-09-07T12:05:00Z",
        ended_at="2026-09-07T12:06:00Z",
        status="failed",
        events=[],
    )
    return {"a": a, "b": b}


def _redaction():
    return load_redaction_policy(REPO_ROOT / "config" / "redaction-policy.json")


def _session_record(
    session_id: str = SESSION_A,
    *,
    started_at: str = "2026-09-07T12:00:00Z",
    event_count: int = 2,
) -> ReplaySession:
    return ReplaySession(
        session_id=session_id,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        started_at=started_at,
        ended_at="2026-09-07T12:01:00Z",
        status="completed",
        event_count=event_count,
        last_sequence=event_count - 1 if event_count else None,
    )


def _snapshot() -> CurrentStateSnapshot:
    spec = CurrentProjectionSpec(analysis_profile_sha256=SHA_C)
    return build_current_snapshot(spec, (_session_record(),))


class CurrentStateModelTests(unittest.TestCase):
    def test_dtos_are_frozen_slotted_and_semantically_validated(self):
        values = (
            CurrentProjectionSpec(analysis_profile_sha256=SHA_C),
            _session_record(),
            _snapshot().metadata,
            _snapshot(),
        )
        for value in values:
            with self.subTest(dto=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                first = fields(type(value))[0].name
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, first, getattr(value, first))
                annotations = " ".join(
                    str(field.type) for field in fields(type(value))
                )
                self.assertNotIn("Path", annotations)

        normalized = ReplaySession(
            session_id=SESSION_A,
            manifest_sha256=SHA_A,
            evidence_sha256=SHA_B,
            started_at="2026-09-07T14:00:00+02:00",
            ended_at="2026-09-07T14:01:00+02:00",
            status="completed",
            event_count=1,
            last_sequence=0,
        )
        self.assertEqual(normalized.started_at, "2026-09-07T12:00:00Z")
        self.assertEqual(normalized.ended_at, "2026-09-07T12:01:00Z")

        with self.assertRaisesRegex(ValueError, "last_sequence"):
            ReplaySession(
                session_id=SESSION_A,
                manifest_sha256=SHA_A,
                evidence_sha256=SHA_B,
                started_at="2026-09-07T12:00:00Z",
                ended_at="2026-09-07T12:01:00Z",
                status="completed",
                event_count=2,
                last_sequence=0,
            )

    def test_ready_and_stale_status_have_distinct_state_identity(self):
        spec = CurrentProjectionSpec(analysis_profile_sha256=SHA_C)
        sessions = (_session_record(),)
        ready = build_current_snapshot(spec, sessions)
        stale = build_current_snapshot(
            spec,
            sessions,
            status="stale",
            stale_reason="incompatible detector structure",
        )

        self.assertEqual(
            ready.metadata.input_fingerprint,
            stale.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            ready.metadata.state_fingerprint,
            stale.metadata.state_fingerprint,
        )
        self.assertEqual(stale.metadata.status, "stale")


class CurrentStateStoreTests(unittest.TestCase):
    def test_database_identity_pragmas_strict_tables_and_read_only_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "current.sqlite3"
            with CurrentStateStore.open_rw(path) as store:
                connection = store._connection
                self.assertEqual(
                    connection.execute("PRAGMA application_id").fetchone()[0],
                    APPLICATION_ID,
                )
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    USER_VERSION,
                )
                self.assertEqual(
                    str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold(),
                    "wal",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA trusted_schema").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    5000,
                )
                strict = {
                    row[1]: row[5]
                    for row in connection.execute("PRAGMA table_list")
                    if row[1] in {"projection_meta", "replayed_session"}
                }
                self.assertEqual(
                    strict,
                    {"projection_meta": 1, "replayed_session": 1},
                )
                store.replace_snapshot(_snapshot())

            with CurrentStateStore.open_read_only_if_exists(path) as read_only:
                self.assertEqual(read_only.snapshot(), _snapshot())
                with self.assertRaises(CurrentStateCompatibilityError):
                    read_only.replace_snapshot(_snapshot())

    def test_foreign_unidentified_newer_and_older_databases_fail_closed(self):
        cases = (
            ("foreign", 1234, USER_VERSION, "foreign"),
            ("newer", APPLICATION_ID, USER_VERSION + 1, "newer"),
            ("older", APPLICATION_ID, 0, "older"),
        )
        for label, app_id, user_version, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "state.sqlite3"
                connection = sqlite3.connect(path)
                connection.execute(f"PRAGMA application_id = {app_id}")
                connection.execute(f"PRAGMA user_version = {user_version}")
                connection.close()
                with self.assertRaisesRegex(
                    CurrentStateCompatibilityError,
                    expected,
                ):
                    CurrentStateStore.open_rw(path)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE foreign_data(x INTEGER)")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(
                CurrentStateCompatibilityError,
                "unidentified",
            ):
                CurrentStateStore.open_rw(path)

    def test_writer_lock_and_transaction_rollback_preserve_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.sqlite3"
            first = CurrentStateStore.open_rw(path)
            second = CurrentStateStore.open_rw(path)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            first.replace_snapshot(_snapshot())

            first._connection.execute("BEGIN IMMEDIATE")
            self.addCleanup(
                lambda: first._connection.execute("ROLLBACK")
                if first._connection.in_transaction
                else None
            )
            second._connection.execute("PRAGMA busy_timeout = 25")
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                second._connection.execute("BEGIN IMMEDIATE")
            first._connection.execute("ROLLBACK")

            before = first.snapshot()
            with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                with first._immediate_transaction():
                    first._connection.execute("DELETE FROM projection_meta")
                    raise RuntimeError("synthetic crash")
            self.assertEqual(first.snapshot(), before)

    def test_corrupted_fingerprint_and_orphan_ledger_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.sqlite3"
            with CurrentStateStore.open_rw(path) as store:
                store.replace_snapshot(_snapshot())
                store._connection.execute(
                    "UPDATE projection_meta SET state_fingerprint = ?",
                    ("0" * 64,),
                )
                with self.assertRaises(CurrentStateIntegrityError):
                    store.snapshot()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.sqlite3"
            store = CurrentStateStore.open_rw(path)
            store._connection.execute(
                """
                INSERT INTO replayed_session VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SESSION_A,
                    SHA_A,
                    SHA_B,
                    "2026-09-07T12:00:00Z",
                    "2026-09-07T12:01:00Z",
                    "completed",
                    1,
                    0,
                ),
            )
            store.close()
            with self.assertRaisesRegex(
                CurrentStateIntegrityError,
                "without metadata",
            ):
                CurrentStateStore.open_rw(path)

    def test_mark_stale_retains_input_identity_and_recomputes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CurrentStateStore.open_rw(
                Path(tmp) / "current.sqlite3"
            ) as store:
                store.replace_snapshot(_snapshot())
                ready = store.snapshot()
                assert ready is not None
                stale = store.mark_stale("detector contract changed")

        self.assertEqual(
            stale.metadata.input_fingerprint,
            ready.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            stale.metadata.state_fingerprint,
            ready.metadata.state_fingerprint,
        )
        self.assertEqual(stale.metadata.status, "stale")


class CurrentStateReplayTests(unittest.TestCase):
    def test_replay_uses_only_finalized_evidence_and_tracks_last_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_replay_fixture(data_dir)
            snapshot = build_replay_snapshot(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )

        self.assertEqual(
            [item.session_id for item in snapshot.sessions],
            [SESSION_B, SESSION_A],
        )
        self.assertEqual(
            [item.event_count for item in snapshot.sessions],
            [1, 2],
        )
        self.assertEqual(
            [item.last_sequence for item in snapshot.sessions],
            [0, 1],
        )
        self.assertEqual(snapshot.metadata.session_count, 2)
        self.assertEqual(snapshot.metadata.last_session_id, SESSION_A)
        self.assertEqual(snapshot.metadata.last_sequence, 1)
        self.assertEqual(snapshot.metadata.status, "ready")
        self.assertEqual(snapshot.metadata.projection_name, PROJECTION_NAME)
        self.assertEqual(snapshot.metadata.projection_version, PROJECTION_VERSION)

    def test_delete_and_replay_produce_identical_semantic_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_replay_fixture(data_dir)

            first = rebuild_current_state(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )
            path = data_dir / "state" / "current.sqlite3"
            first_fingerprint = first.metadata.state_fingerprint
            path.unlink()
            wal = path.with_name(path.name + "-wal")
            shm = path.with_name(path.name + "-shm")
            wal.unlink(missing_ok=True)
            shm.unlink(missing_ok=True)

            second = rebuild_current_state(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )

        self.assertEqual(first, second)
        self.assertEqual(
            first_fingerprint,
            second.metadata.state_fingerprint,
        )

    def test_event_byte_mutation_changes_input_and_state_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            paths = _write_replay_fixture(data_dir)
            first = build_replay_snapshot(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )

            events = [
                _event(
                    SESSION_A,
                    0,
                    EVENT_A0,
                    event_type="fixture.changed",
                ),
                _event(SESSION_A, 1, EVENT_A1),
            ]
            _write_events(paths["a"], events)
            second = build_replay_snapshot(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )

        self.assertNotEqual(
            first.sessions[-1].evidence_sha256,
            second.sessions[-1].evidence_sha256,
        )
        self.assertNotEqual(
            first.metadata.input_fingerprint,
            second.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            first.metadata.state_fingerprint,
            second.metadata.state_fingerprint,
        )

    def test_projection_version_changes_replay_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_replay_fixture(data_dir)
            first = build_replay_snapshot(
                REPO_ROOT,
                data_dir,
                _redaction(),
                projection_version=1,
            )
            second = build_replay_snapshot(
                REPO_ROOT,
                data_dir,
                _redaction(),
                projection_version=2,
            )

        self.assertEqual(
            first.metadata.analysis_profile_sha256,
            second.metadata.analysis_profile_sha256,
        )
        self.assertNotEqual(
            first.metadata.input_fingerprint,
            second.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            first.metadata.state_fingerprint,
            second.metadata.state_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
