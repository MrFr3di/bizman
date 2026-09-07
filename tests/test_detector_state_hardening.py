from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import AnalysisProfile, Finding
from tools.bizman_detector.state import DetectorState, OutboxPayload, StateIntegrityError


SESSION_A = "01991c7d-a400-7000-8000-000000000001"
SESSION_B = "01991c7d-a400-7000-8000-000000000002"


def _identity(
    session_id: str,
    *,
    evidence_sha256: str = "b" * 64,
    ended_at: str = "2026-09-07T12:01:00Z",
) -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=session_id,
        manifest_sha256="a" * 64,
        evidence_sha256=evidence_sha256,
        started_at="2026-09-07T12:00:00Z",
        ended_at=ended_at,
        status="completed",
    )


def _profile(sha256: str) -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256="d" * 64,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256="e" * 64,
        rules=(),
        sha256=sha256,
    )


def _finding() -> Finding:
    return Finding(
        change_id="chg." + "1" * 64,
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("method", "POST"), ("path", "/x")),
        delta=(),
        evidence_event_ids=("event-1",),
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
            "profile": profile.sha256,
            "evidence": identity.evidence_sha256,
            "changes": [item.change_id for item in first_seen],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload_json.encode()).hexdigest()
    return OutboxPayload("bundle." + digest, digest, payload_json, identity.ended_at)


class DetectorStateHardeningTests(unittest.TestCase):
    def test_repeated_change_in_new_session_increments_occurrence_without_new_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            profile = _profile("c" * 64)
            finding = _finding()
            state.process_session_transaction(
                identity=_identity(SESSION_A),
                profile=profile,
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:02:00Z",
            )
            result = state.process_session_transaction(
                identity=_identity(SESSION_B, ended_at="2026-09-07T13:01:00Z"),
                profile=profile,
                findings=(finding,),
                outbox_factory=_payload,
                processed_at="2026-09-07T13:02:00Z",
            )
            self.assertTrue(result.processed)
            self.assertEqual(result.first_seen_change_ids, ())
            self.assertIsNone(result.outbox_bundle_id)
            row = state._connection.execute(
                "SELECT occurrence_count, first_session_id, last_session_id, last_seen_at "
                "FROM changes WHERE analysis_profile_sha256=? AND change_id=?",
                (profile.sha256, finding.change_id),
            ).fetchone()
            self.assertEqual(
                tuple(row),
                (2, SESSION_A, SESSION_B, "2026-09-07T13:01:00Z"),
            )
            self.assertEqual(
                state._connection.execute("SELECT count(*) FROM promotion_outbox").fetchone()[0],
                1,
            )

    def test_same_session_cannot_change_evidence_under_another_analysis_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            state.process_session_transaction(
                identity=_identity(SESSION_A),
                profile=_profile("c" * 64),
                findings=(_finding(),),
                outbox_factory=_payload,
                processed_at="2026-09-07T12:02:00Z",
            )
            with self.assertRaises(StateIntegrityError):
                state.process_session_transaction(
                    identity=_identity(SESSION_A, evidence_sha256="f" * 64),
                    profile=_profile("9" * 64),
                    findings=(_finding(),),
                    outbox_factory=_payload,
                    processed_at="2026-09-07T12:03:00Z",
                )


if __name__ == "__main__":
    unittest.main()
