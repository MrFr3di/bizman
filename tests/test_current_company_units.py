from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bizman.current import (
    APPLICATION_ID,
    USER_VERSION,
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateCompatibilityError,
    CurrentStateIntegrityError,
    CurrentStateOperationError,
    CurrentStateStore,
    ReplaySession,
    UnitState,
    build_current_snapshot,
    build_replay_snapshot,
    rebuild_current_state,
)
from bizman.foundation.fingerprint import canonical_json_bytes
from bizman.foundation.redaction import load_redaction_policy
from bizman.sessions.evidence import EvidenceIntegrityError


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000211"
SESSION_B = "01991c7d-a400-7000-8000-000000000212"
EVENT_A0 = "01991c7d-a400-7000-8000-000000000311"
EVENT_A1 = "01991c7d-a400-7000-8000-000000000312"
EVENT_B0 = "01991c7d-a400-7000-8000-000000000313"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _redaction():
    return load_redaction_policy(REPO_ROOT / "config" / "redaction-policy.json")


def _artifact_bytes(text: str, *, title: str = "Компания Paradise · Предприятия") -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "1.0",
            "sanitizer_version": 2,
            "media_type": "text/html",
            "title": title,
            "text": text,
        }
    )


def _put_artifact(data_dir: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    path = data_dir / "artifacts" / "sha256" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"sha256:{digest}"


def _roster_event(
    session_id: str,
    sequence: int,
    event_id: str,
    ref: str,
    *,
    observed_at: str,
    page: str = "1",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": observed_at,
        "monotonic_time": float(sequence),
        "source": "cdp.network",
        "event_type": "http.response_body",
        "confidence": "observed",
        "request_id": f"r-{sequence}",
        "method": "GET",
        "url_path": "/company/",
        "status_code": 200,
        "query": {"id": ["13393"], "tab": ["units"], "p": [page]},
        "response_body_ref": ref,
    }


def _write_session(
    data_dir: Path,
    *,
    session_id: str,
    started_at: str,
    ended_at: str,
    events: list[dict[str, object]],
) -> None:
    event_rel = f"events/2026-09-29/{session_id}.jsonl"
    event_path = data_dir / event_rel
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for item in events
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "completed",
        "collector": {"name": "bizman-cdp", "version": "0.test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [event_rel],
        "artifact_count": 1,
        "warnings": [],
    }
    manifest_path = data_dir / "sessions" / session_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


class CompanyUnitReplayTests(unittest.TestCase):
    def test_parser_regresses_against_committed_real_company_roster(self):
        pages = REPO_ROOT / "knowledge" / "pages" / "part-002.jsonl"
        page = next(
            json.loads(line)
            for line in pages.read_text(encoding="utf-8").splitlines()
            if line
            and '"path":"/company/"' in line
            and '"id":"13393"' in line
            and '"tab":"units"' in line
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            ref = _put_artifact(
                data_dir,
                _artifact_bytes(page["text"], title=page["title"]),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-29T10:00:00Z",
                ended_at="2026-09-29T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        ref,
                        observed_at="2026-09-29T10:00:30Z",
                        page="2",
                    )
                ],
            )
            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.companies[0].company_id, "13393")
        self.assertEqual(snapshot.companies[0].name, "Paradise")
        by_id = {item.unit_id: item for item in snapshot.units}
        self.assertEqual((by_id["33670"].city_name, by_id["33670"].level), ("Анкара", 1))
        self.assertEqual((by_id["33676"].city_name, by_id["33676"].level), ("Анкара", 1))

    def test_latest_positive_observation_wins_without_partial_page_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            first_ref = _put_artifact(
                data_dir,
                _artifact_bytes(
                    "Компания Paradise\nКомпания\nПредприятия\n"
                    "Город\nПредприятие\nУровень\nЭффект.\n"
                    "Анкара\nДетский магазин #33670\n1\n"
                    "Анкара\nАптека #33676\n1"
                ),
            )
            second_ref = _put_artifact(
                data_dir,
                _artifact_bytes(
                    "Компания Paradise\nКомпания\nПредприятия\n"
                    "Город\nПредприятие\nУровень\nЭффект.\n"
                    "Анкара\nДетский магазин #33670\n3"
                ),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-29T10:00:00Z",
                ended_at="2026-09-29T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        first_ref,
                        observed_at="2026-09-29T10:00:30Z",
                    )
                ],
            )
            _write_session(
                data_dir,
                session_id=SESSION_B,
                started_at="2026-09-29T11:00:00Z",
                ended_at="2026-09-29T11:01:00Z",
                events=[
                    _roster_event(
                        SESSION_B,
                        0,
                        EVENT_B0,
                        second_ref,
                        observed_at="2026-09-29T11:00:30Z",
                        page="2",
                    )
                ],
            )

            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(
            [(item.company_id, item.name) for item in snapshot.companies],
            [("13393", "Paradise")],
        )
        self.assertEqual(
            [
                (item.unit_id, item.level, item.source_session_id)
                for item in snapshot.units
            ],
            [
                ("33670", 3, SESSION_B),
                ("33676", 1, SESSION_A),
            ],
        )
        self.assertEqual(snapshot.metadata.status, "ready")

    def test_successful_write_request_does_not_mutate_observed_unit_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            ref = _put_artifact(
                data_dir,
                _artifact_bytes(
                    "Компания Paradise\nПредприятия\nГород\nПредприятие\nУровень\n"
                    "Анкара\nДетский магазин #33670\n1"
                ),
            )
            events = [
                _roster_event(
                    SESSION_A,
                    0,
                    EVENT_A0,
                    ref,
                    observed_at="2026-09-29T10:00:30Z",
                ),
                {
                    "schema_version": "1.0",
                    "event_id": EVENT_A1,
                    "session_id": SESSION_A,
                    "sequence": 1,
                    "observed_at": "2026-09-29T10:00:40Z",
                    "monotonic_time": 1.0,
                    "source": "cdp.network",
                    "event_type": "http.request",
                    "confidence": "observed",
                    "request_id": "write-1",
                    "method": "POST",
                    "url_path": "/units/level/",
                    "query": {"id": ["33670"]},
                },
            ]
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-29T10:00:00Z",
                ended_at="2026-09-29T10:01:00Z",
                events=events,
            )

            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(len(snapshot.units), 1)
        self.assertEqual(snapshot.units[0].unit_id, "33670")
        self.assertEqual(snapshot.units[0].level, 1)
        self.assertEqual(snapshot.units[0].source_sequence, 0)

    def test_known_roster_parser_drift_marks_snapshot_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            ref = _put_artifact(
                data_dir,
                _artifact_bytes(
                    "Компания Paradise\nПредприятия\nГород\nПредприятие\nУровень\n"
                    "Анкара\nДетский магазин #33670\nUNKNOWN_LAYOUT"
                ),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-29T10:00:00Z",
                ended_at="2026-09-29T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        ref,
                        observed_at="2026-09-29T10:00:30Z",
                    )
                ],
            )
            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.metadata.status, "stale")
        self.assertEqual(
            snapshot.metadata.stale_reason,
            "company_units_parser_v1_incompatible",
        )
        self.assertEqual(snapshot.companies, ())
        self.assertEqual(snapshot.units, ())

    def test_unverified_or_empty_roster_marks_state_stale(self):
        malformed = (
            "Компания Paradise\nПредприятия\nГород\nНовый формат\nУровень",
            "Компания Paradise\nПредприятия\nГород\nПредприятие\nУровень\n"
            "Нет распознанных строк",
        )
        for content in malformed:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp) / "BizManData"
                ref = _put_artifact(data_dir, _artifact_bytes(content))
                _write_session(
                    data_dir,
                    session_id=SESSION_A,
                    started_at="2026-09-29T10:00:00Z",
                    ended_at="2026-09-29T10:01:00Z",
                    events=[
                        _roster_event(
                            SESSION_A,
                            0,
                            EVENT_A0,
                            ref,
                            observed_at="2026-09-29T10:00:30Z",
                        )
                    ],
                )
                snapshot = build_replay_snapshot(
                    REPO_ROOT, data_dir, _redaction()
                )
                self.assertEqual(snapshot.metadata.status, "stale")
                self.assertEqual(
                    snapshot.metadata.stale_reason,
                    "company_units_parser_v1_incompatible",
                )
                self.assertEqual(snapshot.units, ())

    def test_mutated_company_artifact_fails_verified_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            payload = _artifact_bytes(
                "Компания Paradise\nПредприятия\nГород\nПредприятие\nУровень"
            )
            ref = _put_artifact(data_dir, payload)
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-29T10:00:00Z",
                ended_at="2026-09-29T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        ref,
                        observed_at="2026-09-29T10:00:30Z",
                    )
                ],
            )
            digest = ref.split(":", 1)[1]
            artifact = data_dir / "artifacts" / "sha256" / digest[:2] / digest
            artifact.write_bytes(payload + b"tampered")

            redaction = _redaction()
            with self.assertRaises(EvidenceIntegrityError):
                build_replay_snapshot(REPO_ROOT, data_dir, redaction)


def _write_p4a_v1_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        connection.executescript(
            """
            CREATE TABLE projection_meta(
                singleton INTEGER PRIMARY KEY,
                projection_name TEXT,
                projection_version INTEGER,
                analysis_profile_sha256 TEXT,
                input_fingerprint TEXT,
                state_fingerprint TEXT,
                status TEXT,
                stale_reason TEXT,
                session_count INTEGER,
                last_session_id TEXT,
                last_sequence INTEGER
            ) STRICT;
            CREATE TABLE replayed_session(
                session_id TEXT PRIMARY KEY,
                manifest_sha256 TEXT,
                evidence_sha256 TEXT,
                started_at TEXT,
                ended_at TEXT,
                status TEXT,
                event_count INTEGER,
                last_sequence INTEGER
            ) STRICT;
            """
        )
        connection.commit()
    finally:
        connection.close()


class CompanyUnitStoreTests(unittest.TestCase):
    def _snapshot(self):
        session = ReplaySession(
            started_at="2026-09-29T10:00:00Z",
            session_id=SESSION_A,
            manifest_sha256=SHA_A,
            evidence_sha256=SHA_B,
            ended_at="2026-09-29T10:01:00Z",
            status="completed",
            event_count=1,
            last_sequence=0,
        )
        company = CompanyState(
            company_id="13393",
            name="Paradise",
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-09-29T10:00:30Z",
        )
        unit = UnitState(
            unit_id="33670",
            company_id="13393",
            display_name="Детский магазин",
            city_name="Анкара",
            level=1,
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-09-29T10:00:30Z",
        )
        return build_current_snapshot(
            CurrentProjectionSpec(analysis_profile_sha256=SHA_C),
            (session,),
            companies=(company,),
            units=(unit,),
        )

    def test_domain_rows_are_covered_by_state_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.sqlite3"
            with CurrentStateStore.open_rw(path) as store:
                store.replace_snapshot(self._snapshot())
                store._connection.execute(
                    "UPDATE unit SET level = 3 WHERE unit_id = '33670'"
                )
                with self.assertRaises(CurrentStateIntegrityError):
                    store.snapshot()

    def test_explicit_rebuild_does_not_adopt_unrecognized_schema_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            state_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(state_path)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                "CREATE TABLE foreign_data(value TEXT) STRICT"
            )
            connection.commit()
            connection.close()

            redaction = _redaction()
            with self.assertRaises(CurrentStateCompatibilityError):
                rebuild_current_state(REPO_ROOT, data_dir, redaction)

    def test_schema_replacement_refuses_existing_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            _write_p4a_v1_database(state_path)
            before = state_path.read_bytes()
            shm_path = state_path.with_name(state_path.name + "-shm")
            shm_path.write_bytes(b"synthetic-active-sidecar")

            redaction = _redaction()
            with patch(
                "bizman.current.replay._is_rebuildable_older_state",
                return_value=True,
            ):
                with self.assertRaises(CurrentStateOperationError):
                    rebuild_current_state(REPO_ROOT, data_dir, redaction)

            self.assertEqual(state_path.read_bytes(), before)
            self.assertTrue(shm_path.exists())

    def test_failed_atomic_schema_replacement_preserves_p4a_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            _write_p4a_v1_database(state_path)
            before = state_path.read_bytes()

            redaction = _redaction()
            with patch(
                "bizman.current.replay.os.replace",
                side_effect=OSError("synthetic replace failure"),
            ):
                with self.assertRaises(CurrentStateOperationError):
                    rebuild_current_state(REPO_ROOT, data_dir, redaction)

            self.assertEqual(state_path.read_bytes(), before)
            connection = sqlite3.connect(state_path)
            try:
                version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(version, 1)

    def test_explicit_rebuild_replaces_recognized_schema_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            _write_p4a_v1_database(state_path)

            rebuilt = rebuild_current_state(
                REPO_ROOT,
                data_dir,
                _redaction(),
            )
            with CurrentStateStore.open_read_only_if_exists(state_path) as store:
                assert store is not None
                persisted = store.snapshot()
                version = store._connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]

        self.assertEqual(version, USER_VERSION)
        self.assertEqual(rebuilt, persisted)
        self.assertEqual(rebuilt.companies, ())
        self.assertEqual(rebuilt.units, ())


if __name__ == "__main__":
    unittest.main()
