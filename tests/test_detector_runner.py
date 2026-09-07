from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import DiffFact, Finding, MatchState
from tools.bizman_detector.state import TransactionResult
from tools.bizman_foundation.redaction import load_redaction_policy


SESSION_EARLY = "01991c7d-a400-7000-8000-000000000001"
SESSION_LATE = "01991c7d-a400-7000-8000-000000000002"
SESSION_FAILED = "01991c7d-a400-7000-8000-000000000003"
EVENT_ID = "01991c7d-a400-7000-8000-000000000011"


def _identity(session_id: str, started_at: str) -> EvidenceIdentity:
    suffix = session_id[-1]
    return EvidenceIdentity(
        session_id=session_id,
        manifest_sha256=suffix * 64,
        evidence_sha256=("a" if suffix == "1" else "b") * 64,
        started_at=started_at,
        ended_at="2026-09-07T12:10:00Z",
        status="completed",
    )


def _fact(state: MatchState, kind: str) -> DiffFact:
    return DiffFact(
        state=state,
        kind=kind,
        subject=(("path", "/x"),),
        evidence_event_ids=(EVENT_ID,),
    )


def _finding(change: str = "1") -> Finding:
    return Finding(
        change_id="chg." + change * 64,
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        subject=(("path", "/x"),),
        delta=(),
        evidence_event_ids=(EVENT_ID,),
    )


class _FakeReader:
    def __init__(self, statuses, identities, *, recovery_flag=None) -> None:
        self.statuses = tuple(statuses)
        self.identities = identities
        self.recovery_flag = recovery_flag
        self.selected_seen = None
        self.inspected: list[str] = []

    def iter_session_statuses(self, selected=()):
        if self.recovery_flag is not None:
            assert self.recovery_flag["done"], "session scan started before outbox recovery"
        self.selected_seen = tuple(selected)
        yield from self.statuses

    def inspect(self, session_id: str):
        self.inspected.append(session_id)
        return self.identities[session_id]


class _FakeExtractor:
    def __init__(self) -> None:
        self.order: list[str] = []

    def extract(self, identity):
        self.order.append(identity.session_id)
        return identity.session_id


class _FakeDiff:
    def compare(self, observations):
        return (
            _fact(MatchState.KNOWN, "endpoint.known"),
            _fact(MatchState.NOVEL, "endpoint.new"),
        )


class _FakeRules:
    def apply(self, facts):
        return (_finding(),)


class _FakeBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, identity, profile, findings):
        self.calls += 1
        return SimpleNamespace(bundle_id="in-memory")


class _FakeState:
    def __init__(self, *, existing=()) -> None:
        self.existing = frozenset(existing)
        self.process_calls: list[str] = []

    def process_session_transaction(self, **kwargs):
        identity = kwargs["identity"]
        findings = kwargs["findings"]
        self.process_calls.append(identity.session_id)
        return TransactionResult(
            processed=True,
            first_seen_change_ids=tuple(item.change_id for item in findings),
            outbox_bundle_id=None,
        )

    def existing_change_ids(self, analysis_profile_sha256, change_ids):
        return frozenset(change_id for change_id in change_ids if change_id in self.existing)

    def pending_outbox(self):
        return ()


class _FakeMaterializer:
    def __init__(self, flag=None) -> None:
        self.flag = flag
        self.calls = 0

    def materialize_pending(self, *, materialized_at):
        self.calls += 1
        if self.flag is not None:
            self.flag["done"] = True
        return ()


class DetectorRunnerOrderingTests(unittest.TestCase):
    def _profile(self):
        return SimpleNamespace(sha256="c" * 64, baseline_sha256="d" * 64)

    def test_normal_run_recovers_outbox_before_any_session_scan(self):
        from tools.bizman_detector.runner import DetectorRunner

        flag = {"done": False}
        reader = _FakeReader(
            (SimpleNamespace(session_id=SESSION_EARLY, status="completed"),),
            {SESSION_EARLY: _identity(SESSION_EARLY, "2026-09-07T12:00:00Z")},
            recovery_flag=flag,
        )
        materializer = _FakeMaterializer(flag)
        runner = DetectorRunner(
            profile=self._profile(),
            reader=reader,
            extractor=_FakeExtractor(),
            semantic_diff=_FakeDiff(),
            rule_engine=_FakeRules(),
            bundle_builder=_FakeBuilder(),
            state=_FakeState(),
            materializer=materializer,
            selected=(),
            dry_run=False,
            clock=lambda: "2026-09-07T13:00:00Z",
        )
        runner.run()
        self.assertTrue(flag["done"])
        self.assertGreaterEqual(materializer.calls, 1)

    def test_finalized_sessions_sort_by_started_at_and_failed_is_reported(self):
        from tools.bizman_detector.runner import DetectorRunner

        statuses = (
            SimpleNamespace(session_id=SESSION_LATE, status="completed"),
            SimpleNamespace(session_id=SESSION_FAILED, status="failed"),
            SimpleNamespace(session_id=SESSION_EARLY, status="completed"),
        )
        reader = _FakeReader(
            statuses,
            {
                SESSION_LATE: _identity(SESSION_LATE, "2026-09-07T12:05:00Z"),
                SESSION_EARLY: _identity(SESSION_EARLY, "2026-09-07T12:00:00Z"),
            },
        )
        extractor = _FakeExtractor()
        selected = (SESSION_LATE, SESSION_FAILED, SESSION_EARLY)
        runner = DetectorRunner(
            profile=self._profile(),
            reader=reader,
            extractor=extractor,
            semantic_diff=_FakeDiff(),
            rule_engine=_FakeRules(),
            bundle_builder=_FakeBuilder(),
            state=_FakeState(),
            materializer=_FakeMaterializer(),
            selected=selected,
            dry_run=False,
            clock=lambda: "2026-09-07T13:00:00Z",
        )
        summary = runner.run()

        self.assertEqual(reader.selected_seen, selected)
        self.assertEqual(extractor.order, [SESSION_EARLY, SESSION_LATE])
        self.assertEqual(summary.sessions_discovered, 3)
        self.assertEqual(summary.sessions_processed, 2)
        self.assertEqual(summary.sessions_failed_skipped, 1)
        self.assertEqual(summary.fact_counts["known"], 2)
        self.assertEqual(summary.fact_counts["novel"], 2)

    def test_dry_run_classifies_seen_findings_without_write_or_materialization(self):
        from tools.bizman_detector.runner import DetectorRunner

        reader = _FakeReader(
            (SimpleNamespace(session_id=SESSION_EARLY, status="completed"),),
            {SESSION_EARLY: _identity(SESSION_EARLY, "2026-09-07T12:00:00Z")},
        )
        existing = _finding().change_id
        state = _FakeState(existing=(existing,))
        builder = _FakeBuilder()
        runner = DetectorRunner(
            profile=self._profile(),
            reader=reader,
            extractor=_FakeExtractor(),
            semantic_diff=_FakeDiff(),
            rule_engine=_FakeRules(),
            bundle_builder=builder,
            state=state,
            materializer=None,
            selected=(),
            dry_run=True,
            clock=lambda: "2026-09-07T13:00:00Z",
        )
        summary = runner.run()

        self.assertEqual(state.process_calls, [])
        self.assertEqual(builder.calls, 0)
        self.assertEqual(summary.first_seen_change_ids, ())
        self.assertEqual(summary.repeated_change_ids, (existing,))
        self.assertEqual(summary.materialized_bundle_count, 0)


class DetectorRunnerRealDryRunTests(unittest.TestCase):
    def test_from_paths_dry_run_does_not_create_operational_state_or_promotions(self):
        from tools.bizman_detector.runner import DetectorRunner

        repo_root = Path(__file__).resolve().parents[1]
        redaction = load_redaction_policy(repo_root / "config" / "redaction-policy.json")
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            runner = DetectorRunner.from_paths(
                repo_root=repo_root,
                data_dir=data_dir,
                redaction=redaction,
                dry_run=True,
            )
            try:
                summary = runner.run()
            finally:
                runner.close()

            self.assertTrue(summary.dry_run)
            self.assertFalse((data_dir / "detector").exists())
            self.assertFalse((data_dir / "promotions").exists())

    def test_cli_prints_one_machine_readable_json_summary(self):
        from tools.detect_changes import main

        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--repo-root",
                        str(repo_root),
                        "--data-dir",
                        str(data_dir),
                        "--dry-run",
                    ]
                )
            self.assertEqual(exit_code, 0)
            lines = [line for line in output.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            document = __import__("json").loads(lines[0])
            self.assertTrue(document["dry_run"])
            self.assertIn("analysis_profile_sha256", document)
            self.assertIn("fact_counts", document)
            self.assertFalse((data_dir / "detector").exists())
            self.assertFalse((data_dir / "promotions").exists())


if __name__ == "__main__":
    unittest.main()
