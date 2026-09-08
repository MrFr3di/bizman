from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import AnalysisProfile, DiffFact, Finding, MatchState
from tools.bizman_detector.runner import DetectorRunner
from tools.bizman_detector.state import (
    APPLICATION_ID,
    DetectorState,
    StateCompatibilityError,
)


SESSION_A = "01991c7d-a400-7000-8000-000000000101"
SESSION_B = "01991c7d-a400-7000-8000-000000000102"
EVENT_ID = "01991c7d-a400-7000-8000-000000000111"
CHANGE_ID = "chg." + "1" * 64


def _identity(
    session_id: str,
    *,
    started_at: str,
    ended_at: str,
    evidence_sha256: str,
) -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=session_id,
        manifest_sha256=("a" if session_id == SESSION_A else "b") * 64,
        evidence_sha256=evidence_sha256,
        started_at=started_at,
        ended_at=ended_at,
        status="completed",
    )


def _profile() -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256="d" * 64,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256="e" * 64,
        rules=(),
        sha256="c" * 64,
    )


def _finding() -> Finding:
    return Finding(
        change_id=CHANGE_ID,
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("path", "/review-regression"),),
        delta=(),
        evidence_event_ids=(EVENT_ID,),
    )


class _Reader:
    def __init__(self, identities: tuple[EvidenceIdentity, ...]) -> None:
        self.identities = {identity.session_id: identity for identity in identities}

    def iter_session_statuses(self, selected=()):
        session_ids = tuple(selected) if selected else tuple(self.identities)
        for session_id in session_ids:
            yield SimpleNamespace(session_id=session_id, status="completed")

    def inspect(self, session_id: str) -> EvidenceIdentity:
        return self.identities[session_id]


class _Extractor:
    def extract(self, identity: EvidenceIdentity):
        return identity.session_id


class _Diff:
    def compare(self, observations):
        return (
            DiffFact(
                state=MatchState.NOVEL,
                kind="endpoint.new",
                subject=(("path", "/review-regression"),),
                evidence_event_ids=(EVENT_ID,),
            ),
        )


class _Rules:
    def apply(self, facts):
        return (_finding(),)


class _Builder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, identity, profile, findings):
        self.calls += 1
        return SimpleNamespace(bundle_id="in-memory")


class _DryRunState:
    def existing_change_ids(self, analysis_profile_sha256, change_ids):
        return frozenset()

    def pending_outbox(self):
        return ()


class DetectorReviewRegressionTests(unittest.TestCase):
    def test_dry_run_repeated_new_change_is_first_seen_only_once(self):
        identities = (
            _identity(
                SESSION_A,
                started_at="2026-09-07T12:00:00Z",
                ended_at="2026-09-07T12:01:00Z",
                evidence_sha256="1" * 64,
            ),
            _identity(
                SESSION_B,
                started_at="2026-09-07T12:02:00Z",
                ended_at="2026-09-07T12:03:00Z",
                evidence_sha256="2" * 64,
            ),
        )
        builder = _Builder()
        runner = DetectorRunner(
            profile=_profile(),
            reader=_Reader(identities),
            extractor=_Extractor(),
            semantic_diff=_Diff(),
            rule_engine=_Rules(),
            bundle_builder=builder,
            state=_DryRunState(),
            materializer=None,
            selected=(),
            dry_run=True,
            clock=lambda: "2026-09-07T13:00:00Z",
        )

        summary = runner.run()

        self.assertEqual(builder.calls, 1)
        self.assertEqual(summary.first_seen_change_ids, (CHANGE_ID,))
        self.assertEqual(summary.repeated_change_ids, (CHANGE_ID,))

    def test_rejected_foreign_or_unidentified_database_is_not_switched_to_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            foreign = root / "foreign.sqlite3"
            connection = sqlite3.connect(foreign)
            connection.execute("PRAGMA application_id = 1234")
            connection.close()

            with self.assertRaises(StateCompatibilityError):
                DetectorState.open_rw(foreign)
            connection = sqlite3.connect(foreign)
            try:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            finally:
                connection.close()

            unidentified = root / "unidentified.sqlite3"
            connection = sqlite3.connect(unidentified)
            connection.execute("CREATE TABLE unrelated(id INTEGER)")
            connection.close()

            with self.assertRaises(StateCompatibilityError):
                DetectorState.open_rw(unidentified)
            connection = sqlite3.connect(unidentified)
            try:
                self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            finally:
                connection.close()

    def test_repeated_change_never_moves_last_seen_backwards_across_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DetectorState.open_rw(Path(tmp) / "state.sqlite3")
            self.addCleanup(state.close)
            finding = _finding()
            profile = _profile()
            newer = _identity(
                SESSION_A,
                started_at="2026-09-07T08:00:00-04:00",
                ended_at="2026-09-07T09:00:00-04:00",
                evidence_sha256="3" * 64,
            )
            older = _identity(
                SESSION_B,
                started_at="2026-09-07T13:00:00+02:00",
                ended_at="2026-09-07T14:30:00+02:00",
                evidence_sha256="4" * 64,
            )

            state.process_session_transaction(
                identity=newer,
                profile=profile,
                findings=(finding,),
                outbox_factory=lambda identity, profile, first_seen: None,
                processed_at="2026-09-07T13:01:00Z",
            )
            state.process_session_transaction(
                identity=older,
                profile=profile,
                findings=(finding,),
                outbox_factory=lambda identity, profile, first_seen: None,
                processed_at="2026-09-07T13:02:00Z",
            )

            row = state._connection.execute(
                "SELECT occurrence_count, last_session_id, last_seen_at "
                "FROM changes WHERE analysis_profile_sha256=? AND change_id=?",
                (profile.sha256, finding.change_id),
            ).fetchone()
            self.assertEqual(tuple(row), (2, SESSION_A, "2026-09-07T09:00:00-04:00"))


if __name__ == "__main__":
    unittest.main()
