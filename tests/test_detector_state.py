from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import AnalysisProfile, Finding
from tools.bizman_detector.state import (
    APPLICATION_ID,
    USER_VERSION,
    DetectorState,
    OutboxPayload,
    StateCompatibilityError,
    StateIntegrityError,
)


SESSION_ID = "01991c7d-a400-7000-8000-000000000001"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _identity(*, evidence_sha256: str = SHA_B) -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=SESSION_ID,
        manifest_sha256=SHA_A,
        evidence_sha256=evidence_sha256,
        started_at="2026-09-07T12:00:00Z",
        ended_at="2026-09-07T12:01:00Z",
        status="completed",
    )


def _profile(*, sha256: str = SHA_C, baseline_sha256: str = SHA_D) -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256=baseline_sha256,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256="e" * 64,
        rules=(),
        sha256=sha256,
    )


def _finding(*, change_id: str = "chg." + "1" * 64) -> Finding:
    return Finding(
        change_id=change_id,
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("method", "POST"), ("path", "/x")),
        delta=(),
        evidence_event_ids=("event-secret-provenance",),
    )


def _payload(
    identity: EvidenceIdentity,
    profile: AnalysisProfile,
    first_seen: tuple[Finding, ...],
) -> OutboxPayload | None:
    if not first_seen:
        return None
    payload_json = json.dumps(
        {
            "analysis_profile_sha256": profile.sha256,
            "evidence_sha256": identity.evidence_sha256,
            "change_ids": [item.change_id for item in first_seen],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return OutboxPayload(
        bundle_id="bundle." + digest,
        payload_sha256=digest,
        payload_json=payload_json,
        created_at=identity.ended_at,
    )


class DetectorStateConfigurationTests(unittest.TestCase):
    def test_rw_database_is_identified_configured_and_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "detector" / "state.sqlite3"
            state = DetectorState.open_rw(path)
            self.addCleanup(state.close)

            connection = state._connection
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], USER_VERSION)

            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in {"processed_sessions", "changes", "promotion_outbox"}
            }
            self.assertEqual(
                strict,
                {"processed_sessions": 1, "changes": 1, "promotion_outbox": 1},
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (SHA_C, "chg.bad", "r", "not-an-int", "k", "novel", "{}", SESSION_ID,
                     "2026-09-07T12:01:00Z", SESSION_ID, "2026-09-07T12:01:00Z", 1),
                )

    def test_foreign_application_id_and_newer_schema_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "foreign.sqlite3"
            connection = sqlite3.connect(foreign)
            connection.execute("PRAGMA application_id = 1234")
            connection.close()
            with self.assertRaises(StateCompatibilityError):
                DetectorState.open_rw(foreign)

            newer = Path(tmp) / "newer.sqlite3"
            connection = sqlite3.connect(newer)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {USER_VERSION + 1}")
            connection.close()
            with self.assertRaises(StateCompatibilityError):
                DetectorState.open_rw(newer)

    def test_begin_immediate_obtains_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            first = DetectorState.open_rw(path)
            second = DetectorState.open_rw(path)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            first._connection.execute("BEGIN IMMEDIATE")
            self.addCleanup(lambda: first._connection.execute("ROLLBACK") if first._connection.in_transaction else None)
            second._connection.execute("PRAGMA busy_timeout = 25")
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                second._connection.execute("BEGIN IMMEDIATE")


class DetectorStateTransactionTests(unittest.TestCase):
    def test_checkpoint_is_idempotent_per_profile_and_evidence_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            identity = _identity()
            finding = _finding()

            first = state.process_session_transaction(
                identity=identity,
                profile=_profile(),
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:02:00Z",
            )
            self.assertTrue(first.processed)
            self.assertEqual(first.first_seen_change_ids, (finding.change_id,))
            self.assertIsNotNone(first.outbox_bundle_id)

            repeated = state.process_session_transaction(
                identity=identity,
                profile=_profile(),
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:03:00Z",
            )
            self.assertFalse(repeated.processed)

            with self.assertRaises(StateIntegrityError):
                state.process_session_transaction(
                    identity=_identity(evidence_sha256="f" * 64),
                    profile=_profile(),
                    findings=(finding,),
                    outbox_factory=_payload,
                    processed_at="2026-09-07T12:04:00Z",
                )

            replay_profile = _profile(sha256="9" * 64)
            replayed = state.process_session_transaction(
                identity=identity,
                profile=replay_profile,
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:05:00Z",
            )
            self.assertTrue(replayed.processed)

    def test_changes_store_value_free_canonical_identity_and_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            finding = _finding()
            state.process_session_transaction(
                identity=_identity(),
                profile=_profile(),
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:02:00Z",
            )
            row = state._connection.execute(
                "SELECT identity_json, occurrence_count FROM changes WHERE change_id = ?",
                (finding.change_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            identity_json, count = row
            self.assertEqual(count, 1)
            self.assertNotIn("event-secret-provenance", identity_json)
            decoded = json.loads(identity_json)
            self.assertEqual(decoded["rule_id"], "BM-HTTP-001")
            self.assertEqual(decoded["subject"], [["method", "POST"], ["path", "/x"]])

    def test_outbox_and_checkpoint_are_atomic_on_factory_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)

            def fail(
                _identity: EvidenceIdentity,
                _profile: AnalysisProfile,
                _first_seen: tuple[Finding, ...],
            ) -> OutboxPayload | None:
                raise RuntimeError("synthetic failure")

            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                state.process_session_transaction(
                    identity=_identity(),
                    profile=_profile(),
                    findings=(_finding(),),
                    outbox_factory=fail,
                    processed_at="2026-09-07T12:02:00Z",
                )

            self.assertEqual(
                state._connection.execute("SELECT count(*) FROM processed_sessions").fetchone()[0],
                0,
            )
            self.assertEqual(state._connection.execute("SELECT count(*) FROM changes").fetchone()[0], 0)
            self.assertEqual(
                state._connection.execute("SELECT count(*) FROM promotion_outbox").fetchone()[0],
                0,
            )

    def test_pending_outbox_and_materialized_mark_are_integrity_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            result = state.process_session_transaction(
                identity=_identity(),
                profile=_profile(),
                findings=(_finding(),),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:02:00Z",
            )
            pending = state.pending_outbox()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].bundle_id, result.outbox_bundle_id)
            with self.assertRaises(StateIntegrityError):
                state.mark_materialized(
                    pending[0].bundle_id,
                    "0" * 64,
                    "2026-09-07T12:03:00Z",
                )
            state.mark_materialized(
                pending[0].bundle_id,
                pending[0].payload_sha256,
                "2026-09-07T12:03:00Z",
            )
            self.assertEqual(state.pending_outbox(), ())


if __name__ == "__main__":
    unittest.main()
