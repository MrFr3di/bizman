from __future__ import annotations

import unittest

from bizman.telegram import formatting as fmt
from bizman.core import (
    ChangePage,
    ChangeRecord,
    EvidenceTrace,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeSearchResult,
    SessionAnomalyPage,
    SessionAnomalyRecord,
    SessionComparison,
    SessionGetResult,
    SessionPage,
    SessionRecord,
)


def _record(session_id: str, event_count: int) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        manifest_sha256="c" * 64,
        evidence_sha256="d" * 64,
        started_at="2026-09-25T10:00:00Z",
        ended_at="2026-09-25T10:01:00Z",
        status="completed",
        event_count=event_count,
        action_count=1,
        http_request_count=1,
        http_response_count=1,
        correlation_strong_count=1,
        correlation_probable_count=0,
        correlation_temporal_count=0,
        correlation_exact_count=0,
        uncorrelated_action_count=0,
        warning_count=0,
        anomaly_count=0,
    )


class TrimTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(fmt.trim("hello"), "hello")

    def test_long_text_is_bounded_with_suffix(self) -> None:
        text = "x" * 10000
        result = fmt.trim(text)
        self.assertLessEqual(len(result), fmt.MAX_MESSAGE_CHARS)
        self.assertTrue(result.startswith("x" * 100))
        self.assertIn("(+6048 chars truncated)", result)

    def test_trim_prefers_a_line_boundary(self) -> None:
        first = "l" * 500
        tail = "y" * 5000
        text = f"{first}\n{tail}"
        result = fmt.trim(text, limit=600)
        self.assertTrue(result.startswith(first))
        self.assertLessEqual(len(result), 600)
        self.assertIn("(+5001 chars truncated)", result)

    def test_trim_rejects_small_limits(self) -> None:
        with self.assertRaises(ValueError):
            fmt.trim("x", limit=32)


class SessionFormattingTests(unittest.TestCase):
    def test_status_with_sessions(self) -> None:
        text = fmt.format_status(
            SessionPage(items=(_record("01991c7d-a400-7000-8000-000000000011", 7),))
        )
        self.assertIn("Agent Index: OK", text)
        self.assertIn("01991c7d-a400-7000-8000-000000000011", text)
        self.assertIn("events 7", text)

    def test_status_without_sessions(self) -> None:
        self.assertIn("No finalized sessions", fmt.format_status(SessionPage(items=())))

    def test_session_list_is_numbered_and_bounded_hint_on_next_cursor(self) -> None:
        page = SessionPage(items=(_record("s1", 1), _record("s2", 2)), next_cursor="c")
        text = fmt.format_sessions(page)
        self.assertIn("1) s1", text)
        self.assertIn("2) s2", text)
        self.assertIn("first page only", text)

    def test_session_detail_lists_counts(self) -> None:
        text = fmt.format_session(SessionGetResult(session=_record("s1", 42)))
        self.assertIn("Session s1", text)
        self.assertIn("events: 42 (HTTP requests 1, responses 1)", text)
        self.assertIn("warnings: 0, anomalies: 0", text)

    def test_session_detail_missing(self) -> None:
        self.assertEqual(
            fmt.format_session(SessionGetResult(session=None)),
            "Session not found.",
        )

    def test_comparison_uses_signed_deltas(self) -> None:
        comparison = SessionComparison(
            from_session_id="s1",
            from_started_at="2026-09-25T10:00:00Z",
            from_ended_at="2026-09-25T10:01:00Z",
            from_status="completed",
            to_session_id="s2",
            to_started_at="2026-09-25T11:00:00Z",
            to_ended_at="2026-09-25T11:01:00Z",
            to_status="completed",
            event_count_delta=10,
            action_count_delta=-1,
            http_request_count_delta=0,
            http_response_count_delta=0,
            correlation_strong_count_delta=0,
            correlation_probable_count_delta=0,
            correlation_temporal_count_delta=0,
            correlation_exact_count_delta=0,
            uncorrelated_action_count_delta=0,
            warning_count_delta=0,
            anomaly_count_delta=0,
        )
        text = fmt.format_comparison(comparison)
        self.assertIn("delta = to - from", text)
        self.assertIn("events +10", text)
        self.assertIn("actions -1", text)


class ChangeFormattingTests(unittest.TestCase):
    def _change(self, profile: str) -> ChangeRecord:
        return ChangeRecord(
            analysis_profile_sha256=profile,
            change_id="chg.a",
            rule_id="BM-HTTP-001",
            rule_version=1,
            kind="endpoint.new",
            novelty_class="novel",
            first_session_id="s1",
            first_seen_at="2026-09-25T10:00:30Z",
            last_session_id="s1",
            last_seen_at="2026-09-25T10:00:30Z",
            occurrence_count=1,
        )

    def test_changes_page_lists_and_labels_profiles(self) -> None:
        text = fmt.format_changes(
            ChangePage(items=(self._change("a" * 64), self._change("b" * 64)))
        )
        self.assertIn("Changes (first 10):", text)
        self.assertIn("1) chg.a", text)
        self.assertIn("profile " + "a" * 12 + "…", text)
        self.assertIn("rule BM-HTTP-001 v1 | endpoint.new | novel", text)

    def test_change_detail_keeps_full_profile(self) -> None:
        profile = "b" * 64
        text = fmt.format_change(self._change(profile))
        self.assertIn(f"profile {profile}", text)
        self.assertIn("Change chg.a", text)

    def test_changes_empty(self) -> None:
        self.assertEqual(fmt.format_changes(ChangePage(items=())), "No changes recorded.")


class AnomalyFormattingTests(unittest.TestCase):
    def test_anomalies_page(self) -> None:
        record = SessionAnomalyRecord(
            session_id="s1",
            started_at="2026-09-25T10:00:00Z",
            ended_at="2026-09-25T10:01:00Z",
            status="completed",
            warning_count=2,
            anomaly_count=1,
            uncorrelated_action_count=3,
        )
        text = fmt.format_anomalies(SessionAnomalyPage(items=(record,)))
        self.assertIn("Sessions with signals (latest 10):", text)
        self.assertIn("warnings 2 | anomalies 1 | uncorrelated 3", text)

    def test_anomalies_empty(self) -> None:
        self.assertIn(
            "No sessions with warning/anomaly",
            fmt.format_anomalies(SessionAnomalyPage(items=())),
        )


class KnowledgeFormattingTests(unittest.TestCase):
    def test_search_lists_hits(self) -> None:
        hit = KnowledgeHit(
            ref="bm.city.1",
            kind="city",
            title="Alpha City",
            match_kind="exact_title",
            evidence_refs=("src.test#1",),
        )
        text = fmt.format_search("Alpha", KnowledgeSearchResult(items=(hit,)))
        self.assertIn('Knowledge search "Alpha" (top 1):', text)
        self.assertIn("1) [city] bm.city.1 — Alpha City (exact_title)", text)

    def test_search_no_match(self) -> None:
        text = fmt.format_search("zzz", KnowledgeSearchResult(items=()))
        self.assertIn('No knowledge matched "zzz"', text)

    def test_item_body_and_evidence_bounds(self) -> None:
        item = KnowledgeItem(
            ref="bm.city.1",
            kind="city",
            title="Alpha City",
            aliases=("Alpha",),
            body="b" * 2000,
            evidence_refs=tuple(f"src.test#{index}" for index in range(1, 12)),
            source_dataset="tests.synthetic",
        )
        text = fmt.format_knowledge_item(item)
        self.assertIn("[bm.city.1] kind=city", text)
        self.assertIn("Aliases: Alpha", text)
        self.assertIn("…", text)
        self.assertIn("(+3 more)", text)

    def test_item_without_aliases_or_evidence(self) -> None:
        item = KnowledgeItem(
            ref="bm.city.1",
            kind="city",
            title="Alpha City",
            aliases=(),
            body="capital",
            evidence_refs=(),
            source_dataset="tests.synthetic",
        )
        text = fmt.format_knowledge_item(item)
        self.assertIn("Aliases: none", text)
        self.assertIn("Evidence refs: none", text)


class TraceFormattingTests(unittest.TestCase):
    def test_har_trace(self) -> None:
        trace = EvidenceTrace(
            evidence_ref="src.har.bizmania.2026-09-06.01#entry-224",
            source_id="src.har.bizmania.2026-09-06.01",
            source_kind="har_capture",
            locator_kind="entry",
            ordinal=224,
            source_record_count=17147,
            raw_source_committed=False,
            source_sha256="a" * 64,
            runtime_session_id=None,
            observed_from="2026-09-06T00:00:00Z",
            observed_to="2026-09-06T01:00:00Z",
            privacy="sanitized",
            provenance_policy="policy",
        )
        text = fmt.format_trace(trace)
        self.assertIn("source: src.har.bizmania.2026-09-06.01 (har_capture)", text)
        self.assertIn("locator: entry 224 of 17147 records", text)
        self.assertIn("observed: 2026-09-06T00:00:00Z .. 2026-09-06T01:00:00Z", text)
        self.assertIn("raw source committed: no", text)
        self.assertNotIn("runtime session:", text)

    def test_promoted_trace_includes_runtime_session(self) -> None:
        trace = EvidenceTrace(
            evidence_ref="live-cdp-2026-09-07#seq-25730",
            source_id="live-cdp-2026-09-07",
            source_kind="promoted_session",
            locator_kind="sequence",
            ordinal=25730,
            source_record_count=32128,
            raw_source_committed=False,
            source_sha256=None,
            runtime_session_id="01a07d18-cf20-7207-bbf7-d5fa8a87f103",
            observed_from="2026-09-07T18:19:33Z",
            observed_to=None,
            privacy="sanitized",
            provenance_policy="policy",
        )
        text = fmt.format_trace(trace)
        self.assertIn("locator: sequence 25730 of 32128 records", text)
        self.assertIn(
            "runtime session: 01a07d18-cf20-7207-bbf7-d5fa8a87f103", text
        )


if __name__ == "__main__":
    unittest.main()