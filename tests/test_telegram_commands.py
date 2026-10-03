from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from bizman.core import CoreContext, RepositoryAssets
from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.knowledge import KnowledgeProjection
from bizman.readmodel.model import KnowledgeRecord, RefKind
from bizman.readmodel.runtime import ChangeIndexRecord, RuntimeProjection, SessionSummary
from bizman.readmodel.store import rebuild_agent_index
from bizman.telegram import commands

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_A = "a" * 64
PROFILE_B = "b" * 64
SESSION_A = "01991c7d-a400-7000-8000-000000000011"
SESSION_B = "01991c7d-a400-7000-8000-000000000012"
SESSION_C = "01991c7d-a400-7000-8000-000000000013"
SESSION_D = "01991c7d-a400-7000-8000-000000000014"
MISSING_SESSION = "01991c7d-a400-7000-8000-000000000099"


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value


def _context(data_dir: Path) -> CoreContext:
    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=FixedClock(datetime(2026, 9, 25, 12, 0, tzinfo=UTC)),
    )


def _knowledge_projection() -> KnowledgeProjection:
    records = (
        KnowledgeRecord(
            ref="bm.city.1",
            kind=RefKind.CITY,
            title="Alpha City",
            aliases=("Alpha",),
            body="capital alpha city",
            evidence_refs=("src.test#1",),
            source_dataset="tests.synthetic",
        ),
        KnowledgeRecord(
            ref="bm.product.1",
            kind=RefKind.PRODUCT,
            title="Widget",
            aliases=("Test Widget",),
            body="industrial widget",
            evidence_refs=("src.test#2",),
            source_dataset="tests.synthetic",
        ),
    )
    semantics = [
        {
            "ref": item.ref,
            "kind": item.kind.value,
            "title": item.title,
            "aliases": list(item.aliases),
            "body": item.body,
            "evidence_refs": list(item.evidence_refs),
            "source_dataset": item.source_dataset,
        }
        for item in sorted(records, key=lambda item: item.ref)
    ]
    return KnowledgeProjection(
        records=records,
        source_fingerprint=canonical_sha256(semantics),
    )


def _session(
    session_id: str,
    *,
    started_at: str,
    ended_at: str,
    event_count: int,
    action_count: int = 1,
    warning_count: int = 0,
    anomaly_count: int = 0,
    uncorrelated_action_count: int = 0,
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        manifest_sha256="c" * 64,
        evidence_sha256="d" * 64,
        started_at=started_at,
        ended_at=ended_at,
        status="completed",
        event_count=event_count,
        action_count=action_count,
        http_request_count=1,
        http_response_count=1,
        correlation_strong_count=action_count - uncorrelated_action_count,
        correlation_probable_count=0,
        correlation_temporal_count=0,
        correlation_exact_count=0,
        uncorrelated_action_count=uncorrelated_action_count,
        warning_count=warning_count,
        anomaly_count=anomaly_count,
    )


def _change(profile: str, suffix: str, first_session_id: str, first_seen_at: str):
    return ChangeIndexRecord(
        analysis_profile_sha256=profile,
        change_id=f"chg.{suffix}",
        rule_id="BM-HTTP-001",
        rule_version=1,
        kind="endpoint.new",
        novelty_class="novel",
        first_session_id=first_session_id,
        first_seen_at=first_seen_at,
        last_session_id=first_session_id,
        last_seen_at=first_seen_at,
        occurrence_count=1,
    )


def _runtime_projection() -> RuntimeProjection:
    return RuntimeProjection(
        sessions=(
            _session(SESSION_A, started_at="2026-09-25T10:00:00Z", ended_at="2026-09-25T10:01:00Z", event_count=10),
            _session(SESSION_B, started_at="2026-09-25T10:01:00Z", ended_at="2026-09-25T10:02:00Z", event_count=20),
            _session(SESSION_C, started_at="2026-09-25T10:01:00Z", ended_at="2026-09-25T10:03:00Z", event_count=30),
            _session(
                SESSION_D,
                started_at="2026-09-25T10:02:00Z",
                ended_at="2026-09-25T10:04:00Z",
                event_count=40,
                action_count=5,
                warning_count=2,
                anomaly_count=1,
                uncorrelated_action_count=3,
            ),
        ),
        changes=(
            _change(PROFILE_A, "a", SESSION_A, "2026-09-25T10:00:30Z"),
            _change(PROFILE_A, "b", SESSION_B, "2026-09-25T10:01:30Z"),
            _change(PROFILE_B, "a", SESSION_C, "2026-09-25T10:02:30Z"),
        ),
    )


def _build_index(data_dir: Path) -> None:
    rebuild_agent_index(
        data_dir / "index" / "agent-index.sqlite3",
        _knowledge_projection(),
        _runtime_projection(),
        completed_at="2026-09-25T12:00:00Z",
    )


class TelegramCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "BizManData"
        _build_index(self.data_dir)
        self.context = _context(self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_command(self, text: str) -> str:
        reply = commands.execute(self.context, text)
        self.assertNotIn(str(self.data_dir), reply)
        self.assertNotIn(str(REPO_ROOT), reply)
        return reply

    def test_execute_requires_context_type(self) -> None:
        with self.assertRaises(TypeError):
            commands.execute(None, "/help")

    def test_help_and_empty_message_show_commands(self) -> None:
        for text in ("/help", "", "   "):
            with self.subTest(text=text):
                reply = self.run_command(text)
                self.assertIn("/status", reply)
                self.assertIn("/trace <evidence_ref>", reply)

    def test_unknown_command_is_sanitized(self) -> None:
        reply = self.run_command("/nope")
        self.assertIn("Unknown command: nope", reply)

    def test_status_reports_latest_session(self) -> None:
        reply = self.run_command("/status")
        self.assertIn("Agent Index: OK", reply)
        self.assertIn(SESSION_A, reply)
        self.assertIn("events 10", reply)

    def test_sessions_lists_latest_first_page(self) -> None:
        reply = self.run_command("/sessions")
        self.assertIn(SESSION_A, reply)
        self.assertIn(SESSION_D, reply)
        self.assertIn("warnings 2 | anomalies 1", reply)
        reply_with_args = self.run_command("/sessions 2")
        self.assertIn("Invalid arguments", reply_with_args)

    def test_session_detail_not_found_and_usage(self) -> None:
        self.assertIn("Session not found.", self.run_command(f"/session {MISSING_SESSION}"))
        self.assertIn("Invalid arguments", self.run_command("/session not-a-uuid"))

    def test_compare_reports_signed_deltas_and_missing_sessions(self) -> None:
        reply = self.run_command(f"/compare {SESSION_A} {SESSION_B}")
        self.assertIn("delta = to - from", reply)
        self.assertIn("events +10", reply)
        missing = self.run_command(f"/compare {SESSION_A} {MISSING_SESSION}")
        self.assertIn(MISSING_SESSION, missing)
        self.assertIn("missing sessions", missing)
        self.assertIn("Invalid arguments", self.run_command("/compare one two"))

    def test_anomalies_lists_signaled_sessions(self) -> None:
        reply = self.run_command("/anomalies")
        self.assertIn(SESSION_D, reply)
        self.assertIn("warnings 2 | anomalies 1 | uncorrelated 3", reply)
        self.assertNotIn(SESSION_A, reply)

    def test_changes_scoped_and_unscoped(self) -> None:
        unscoped = self.run_command("/changes")
        self.assertIn("chg.a", unscoped)
        self.assertIn("chg.b", unscoped)
        self.assertIn(PROFILE_A[:12] + "…", unscoped)
        scoped = self.run_command(f"/changes {PROFILE_A}")
        self.assertIn("chg.a", scoped)
        self.assertIn("chg.b", scoped)
        self.assertNotIn(PROFILE_B[:12] + "…", scoped)
        self.assertIn("Invalid arguments", self.run_command("/changes nothex"))

    def test_change_get_and_errors(self) -> None:
        reply = self.run_command(f"/change {PROFILE_A} chg.a")
        self.assertIn("Change chg.a", reply)
        self.assertIn(PROFILE_A, reply)
        self.assertIn(
            "Change not found",
            self.run_command(f"/change {PROFILE_B} chg.zzz"),
        )
        self.assertIn("Invalid arguments", self.run_command("/change only-one-arg"))

    def test_knowledge_resolve_then_search_then_no_match(self) -> None:
        by_ref = self.run_command("/k bm.city.1")
        self.assertIn("bm.city.1", by_ref)
        self.assertIn("(exact_ref)", by_ref)
        by_title = self.run_command("/k Alpha")
        self.assertIn("bm.city.1", by_title)
        by_fts = self.run_command("/k industrial")
        self.assertIn("bm.product.1", by_fts)
        self.assertIn("(full_text)", by_fts)
        no_match = self.run_command("/k zzzqqqxyz")
        self.assertIn("No knowledge matched", no_match)
        self.assertIn("Invalid arguments", self.run_command("/k"))

    def test_knowledge_get(self) -> None:
        reply = self.run_command("/kb bm.city.1")
        self.assertIn("[bm.city.1] kind=city", reply)
        self.assertIn("Aliases: Alpha", reply)
        self.assertIn("capital alpha city", reply)
        self.assertIn("Knowledge item not found", self.run_command("/kb bm.city.999"))
        self.assertIn("Invalid arguments", self.run_command("/kb 'weird ref'"))

    def test_trace_har_and_promoted_and_missing(self) -> None:
        har = self.run_command("/trace src.har.bizmania.2026-09-06.01#entry-224")
        self.assertIn("har_capture", har)
        self.assertIn("entry 224 of", har)
        promoted = self.run_command("/trace live-cdp-2026-09-07#seq-25730")
        self.assertIn("promoted_session", promoted)
        self.assertIn("runtime session:", promoted)
        self.assertIn(
            "Evidence reference not found",
            self.run_command("/trace src.zzz#entry-1"),
        )
        self.assertIn("Invalid arguments", self.run_command("/trace bad ref"))

    def test_usage_error_reply_keeps_usage(self) -> None:
        reply = self.run_command("/compare")
        self.assertIn("Usage: /compare <uuidv7> <uuidv7>", reply)


if __name__ == "__main__":
    unittest.main()
