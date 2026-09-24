from __future__ import annotations

import ast
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import unittest

from bizman.changes import ChangeSummaryReader
from bizman.changes.model import AnalysisProfile, Finding
from bizman.changes.state import DetectorState
from bizman.readmodel import (
    INDEX_APPLICATION_ID,
    INDEX_SCHEMA_VERSION,
    KnowledgeIndex,
    RuntimeProjection,
    project_curated_knowledge,
    project_runtime_intelligence,
    rebuild_agent_index,
)
from bizman.readmodel.store import ReadModelCompatibilityError, ReadModelIntegrityError
from bizman.sessions import EvidenceReader


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000011"
SESSION_B = "01991c7d-a400-7000-8000-000000000012"
ACTION_A = "01991c7d-a400-7000-8000-000000000101"
ACTION_B = "01991c7d-a400-7000-8000-000000000102"
REQUEST = "01991c7d-a400-7000-8000-000000000103"
RESPONSE = "01991c7d-a400-7000-8000-000000000104"
CORRELATION = "01991c7d-a400-7000-8000-000000000105"
FAILED = "01991c7d-a400-7000-8000-000000000106"
SESSION_B_EVENT = "01991c7d-a400-7000-8000-000000000107"
PROFILE_A = "a" * 64
PROFILE_B = "b" * 64
CHANGE_ID = "chg." + hashlib.sha256(b"runtime-change").hexdigest()
FIXED_COMPLETED_AT = "2026-09-24T12:00:00Z"


def _event(
    session_id: str,
    event_id: str,
    sequence: int,
    *,
    source: str,
    event_type: str,
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": "2026-09-24T10:00:00Z",
        "monotonic_time": float(sequence),
        "source": source,
        "event_type": event_type,
        "confidence": "inferred" if source == "system" else "observed",
    }
    value.update(extra)
    return value


def _session_a_events() -> list[dict[str, object]]:
    return [
        _event(
            SESSION_A,
            ACTION_A,
            0,
            source="dom.action",
            event_type="dom.action",
            action_kind="submit",
            action_refs=[],
        ),
        _event(
            SESSION_A,
            REQUEST,
            1,
            source="cdp.network",
            event_type="http.request",
            request_id="r1",
            method="POST",
            url_path="/api/post",
            action_refs=[],
        ),
        _event(
            SESSION_A,
            RESPONSE,
            2,
            source="cdp.network",
            event_type="http.response",
            request_id="r1",
            method="POST",
            url_path="/api/post",
            status_code=200,
            action_refs=[],
        ),
        _event(
            SESSION_A,
            CORRELATION,
            3,
            source="system",
            event_type="correlation.action_http",
            action_refs=[ACTION_A],
            action_event_id=ACTION_A,
            network_event_id=REQUEST,
            correlation_status="strong",
            correlation_score=0.95,
            delta_ms=25.0,
            signals=["form-path-match"],
        ),
        _event(
            SESSION_A,
            ACTION_B,
            4,
            source="dom.action",
            event_type="dom.action",
            action_kind="click",
            action_refs=[],
        ),
        _event(
            SESSION_A,
            FAILED,
            5,
            source="cdp.network",
            event_type="http.failed",
            request_id="r2",
            method="GET",
            url_path="/failed",
            action_refs=[],
        ),
    ]


def _manifest(
    session_id: str,
    *,
    started_at: str,
    ended_at: str,
    event_rel: str,
    warnings: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "completed",
        "collector": {"name": "bizman-cdp", "version": "test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [event_rel],
        "artifact_count": 0,
        "warnings": warnings,
    }


def _write_session(
    data_dir: Path,
    session_id: str,
    events: list[dict[str, object]],
    *,
    started_at: str,
    ended_at: str,
    warnings: list[str] | None = None,
) -> None:
    event_rel = f"events/2026-09-24/{session_id}.jsonl"
    event_path = data_dir.joinpath(*PurePosixPath(event_rel).parts)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(
        "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    manifest_path = data_dir / "sessions" / session_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            _manifest(
                session_id,
                started_at=started_at,
                ended_at=ended_at,
                event_rel=event_rel,
                warnings=list(warnings or []),
            ),
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _profile(sha256: str) -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256="c" * 64,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256="d" * 64,
        rules=(),
        sha256=sha256,
    )


def _finding() -> Finding:
    return Finding(
        change_id=CHANGE_ID,
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("method", "POST"), ("path", "/api/post")),
        delta=(),
        evidence_event_ids=(REQUEST,),
    )


class RuntimeProjectionTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[EvidenceReader, Path]:
        data_dir = root / "BizManData"
        _write_session(
            data_dir,
            SESSION_A,
            _session_a_events(),
            started_at="2026-09-24T10:00:00Z",
            ended_at="2026-09-24T10:01:00Z",
            warnings=["synthetic collector warning"],
        )
        _write_session(
            data_dir,
            SESSION_B,
            [
                _event(
                    SESSION_B,
                    SESSION_B_EVENT,
                    0,
                    source="system",
                    event_type="fixture.event",
                )
            ],
            started_at="2026-09-24T10:02:00Z",
            ended_at="2026-09-24T10:03:00Z",
        )
        return EvidenceReader(REPO_ROOT, data_dir), data_dir / "detector" / "state.sqlite3"

    def _detector_state(self, reader: EvidenceReader, path: Path) -> None:
        identity_a = reader.inspect(SESSION_A)
        identity_b = reader.inspect(SESSION_B)
        finding = _finding()
        with DetectorState.open_rw(path) as state:
            state.process_session_transaction(
                identity=identity_a,
                profile=_profile(PROFILE_A),
                findings=(finding,),
                outbox_factory=lambda identity, profile, findings: None,
                processed_at="2026-09-24T10:04:00Z",
            )
            state.process_session_transaction(
                identity=identity_b,
                profile=_profile(PROFILE_A),
                findings=(finding,),
                outbox_factory=lambda identity, profile, findings: None,
                processed_at="2026-09-24T10:05:00Z",
            )
            state.process_session_transaction(
                identity=identity_a,
                profile=_profile(PROFILE_B),
                findings=(finding,),
                outbox_factory=lambda identity, profile, findings: None,
                processed_at="2026-09-24T10:06:00Z",
            )

    def test_session_projection_uses_verified_identity_and_expected_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader, _ = self._fixture(Path(tmp))
            projection = project_runtime_intelligence(reader)
            self.assertEqual(len(projection.sessions), 2)

            item = next(value for value in projection.sessions if value.session_id == SESSION_A)
            self.assertEqual(item.manifest_sha256, reader.inspect(SESSION_A).manifest_sha256)
            self.assertEqual(item.evidence_sha256, reader.inspect(SESSION_A).evidence_sha256)
            self.assertEqual(item.event_count, 6)
            self.assertEqual(item.action_count, 2)
            self.assertEqual(item.http_request_count, 1)
            self.assertEqual(item.http_response_count, 1)
            self.assertEqual(item.correlation_strong_count, 1)
            self.assertEqual(item.correlation_probable_count, 0)
            self.assertEqual(item.correlation_temporal_count, 0)
            self.assertEqual(item.correlation_exact_count, 0)
            self.assertEqual(item.uncorrelated_action_count, 1)
            self.assertEqual(item.warning_count, 1)
            self.assertEqual(item.anomaly_count, 1)

    def test_change_projection_preserves_profile_scope_and_occurrence_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader, state_path = self._fixture(Path(tmp))
            self._detector_state(reader, state_path)
            change_reader = ChangeSummaryReader(state_path)
            self.addCleanup(change_reader.close)

            projection = project_runtime_intelligence(reader, change_reader)
            self.assertEqual(
                [(item.analysis_profile_sha256, item.change_id) for item in projection.changes],
                [(PROFILE_A, CHANGE_ID), (PROFILE_B, CHANGE_ID)],
            )
            first, second = projection.changes
            self.assertEqual(first.occurrence_count, 2)
            self.assertEqual(first.first_session_id, SESSION_A)
            self.assertEqual(first.last_session_id, SESSION_B)
            self.assertEqual(second.occurrence_count, 1)
            self.assertEqual(second.first_session_id, SESSION_A)
            self.assertEqual(second.last_session_id, SESSION_A)

    def test_rebuild_is_deterministic_and_typed_runtime_reads_are_profile_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reader, state_path = self._fixture(root)
            self._detector_state(reader, state_path)
            with ChangeSummaryReader(state_path) as change_reader:
                runtime = project_runtime_intelligence(reader, change_reader)

            knowledge = project_curated_knowledge(REPO_ROOT)
            paths = (root / "first.sqlite3", root / "second.sqlite3")
            generations = tuple(
                rebuild_agent_index(
                    path,
                    knowledge,
                    runtime,
                    completed_at=FIXED_COMPLETED_AT,
                )
                for path in paths
            )
            self.assertEqual(generations[0], generations[1])

            with KnowledgeIndex(paths[0]) as index:
                meta = index.metadata()
                sessions = index.session_summaries()
                profile_a = index.change_records(PROFILE_A)
                all_changes = index.change_records()

            self.assertEqual(meta["schema_version"], "2")
            self.assertEqual(meta["session_count"], "2")
            self.assertEqual(meta["change_count"], "2")
            self.assertEqual(meta["runtime_fingerprint"], runtime.source_fingerprint)
            self.assertEqual(sessions, runtime.sessions)
            self.assertEqual(profile_a, (runtime.changes[0],))
            self.assertEqual(all_changes, runtime.changes)

    def test_runtime_fingerprint_detects_persisted_row_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reader, _ = self._fixture(root)
            runtime = project_runtime_intelligence(reader)
            path = root / "agent-index.sqlite3"
            rebuild_agent_index(
                path,
                project_curated_knowledge(REPO_ROOT),
                runtime,
                completed_at=FIXED_COMPLETED_AT,
            )
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE session_summary SET warning_count = warning_count + 1 "
                    "WHERE session_id = ?",
                    (SESSION_A,),
                )
                connection.commit()

            with self.assertRaisesRegex(ReadModelIntegrityError, "runtime fingerprint"):
                KnowledgeIndex(path)

    def test_pre_p2c_schema_is_explicitly_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema-v1.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"PRAGMA application_id = {INDEX_APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            self.assertEqual(INDEX_SCHEMA_VERSION, 2)
            with self.assertRaisesRegex(ReadModelCompatibilityError, "schema 1"):
                KnowledgeIndex(path)

    def test_runtime_projection_rejects_wrong_record_types_cleanly(self):
        with self.assertRaisesRegex(TypeError, "SessionSummary"):
            RuntimeProjection(sessions=(object(),))
        with self.assertRaisesRegex(TypeError, "ChangeIndexRecord"):
            RuntimeProjection(changes=(object(),))

    def test_readmodel_runtime_has_no_raw_event_or_detector_sql_dependency(self):
        path = REPO_ROOT / "src/bizman/readmodel/runtime.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        self.assertNotIn("sqlite3", imported)
        self.assertNotIn("bizman.changes.state", imported)
        self.assertNotIn("pathlib", imported)
        self.assertNotIn("events/", source)
        self.assertNotIn(".jsonl", source)


if __name__ == "__main__":
    unittest.main()
