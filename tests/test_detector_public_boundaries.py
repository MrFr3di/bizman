from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity, EvidenceReader
from tools.bizman_detector.model import AnalysisProfile, Finding
from tools.bizman_detector.state import DetectorState


SESSION_COMPLETED = "01991c7d-a400-7000-8000-000000000001"
SESSION_FAILED = "01991c7d-a400-7000-8000-000000000002"
SESSION_RUNNING = "01991c7d-a400-7000-8000-000000000003"
EVENT_ID = "01991c7d-a400-7000-8000-000000000010"


def _manifest(session_id: str, status: str, started_at: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": None if status == "running" else "2026-09-07T12:10:00Z",
        "status": status,
        "collector": {"name": "bizman-cdp", "version": "test"},
        "browser": {"product": "Chrome", "version": "test"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [],
        "artifact_count": 0,
        "warnings": [],
    }


def _profile() -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256="a" * 64,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256="b" * 64,
        rules=(),
        sha256="c" * 64,
    )


def _identity() -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=SESSION_COMPLETED,
        manifest_sha256="d" * 64,
        evidence_sha256="e" * 64,
        started_at="2026-09-07T12:00:00Z",
        ended_at="2026-09-07T12:10:00Z",
        status="completed",
    )


def _finding(index: int) -> Finding:
    digest = hashlib.sha256(f"change-{index}".encode("ascii")).hexdigest()
    return Finding(
        change_id=f"chg.{digest}",
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("path", f"/new/{index}"),),
        delta=(),
        evidence_event_ids=(EVENT_ID,),
    )


class EvidenceSessionStatusBoundaryTests(unittest.TestCase):
    def test_status_enumeration_is_validated_and_does_not_require_event_replay(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            sessions = data_dir / "sessions"
            for session_id, status, started_at in (
                (SESSION_RUNNING, "running", "2026-09-07T12:02:00Z"),
                (SESSION_COMPLETED, "completed", "2026-09-07T12:00:00Z"),
                (SESSION_FAILED, "failed", "2026-09-07T12:01:00Z"),
            ):
                directory = sessions / session_id
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "manifest.json").write_text(
                    json.dumps(_manifest(session_id, status, started_at)),
                    encoding="utf-8",
                )

            reader = EvidenceReader(repo_root, data_dir)
            statuses = tuple(reader.iter_session_statuses())
            self.assertEqual(
                [(item.session_id, item.status) for item in statuses],
                [
                    (SESSION_COMPLETED, "completed"),
                    (SESSION_FAILED, "failed"),
                    (SESSION_RUNNING, "running"),
                ],
            )
            self.assertEqual(statuses[0].started_at, "2026-09-07T12:00:00Z")
            self.assertEqual(statuses[0].ended_at, "2026-09-07T12:10:00Z")
            self.assertIsNone(statuses[2].ended_at)

            selected = tuple(reader.iter_session_statuses((SESSION_FAILED, SESSION_COMPLETED)))
            self.assertEqual(
                [item.session_id for item in selected],
                [SESSION_COMPLETED, SESSION_FAILED],
            )


class DetectorStateReadBoundaryTests(unittest.TestCase):
    def test_existing_change_lookup_is_public_exact_and_chunked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "detector" / "state.sqlite3"
            findings = tuple(_finding(index) for index in range(450))
            with DetectorState.open_rw(path) as state:
                state.process_session_transaction(
                    identity=_identity(),
                    profile=_profile(),
                    findings=findings,
                    outbox_factory=lambda identity, profile, first_seen: None,
                    processed_at="2026-09-07T12:11:00Z",
                )

            requested = tuple(finding.change_id for finding in findings) + tuple(
                f"chg.{hashlib.sha256(f'missing-{index}'.encode('ascii')).hexdigest()}"
                for index in range(75)
            )
            with DetectorState.open_read_only_if_exists(path) as state:
                assert state is not None
                existing = state.existing_change_ids(_profile().sha256, requested)
            self.assertEqual(existing, frozenset(finding.change_id for finding in findings))


if __name__ == "__main__":
    unittest.main()
