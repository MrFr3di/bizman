from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.knowledge import KnowledgeProjection
from bizman.readmodel.model import KnowledgeRecord, RefKind
from bizman.readmodel.runtime import ChangeIndexRecord, RuntimeProjection, SessionSummary
from bizman.readmodel.store import rebuild_agent_index


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_A = "a" * 64
PROFILE_B = "b" * 64
SESSION_A = "01991c7d-a400-7000-8000-000000000011"
SESSION_B = "01991c7d-a400-7000-8000-000000000012"
SESSION_C = "01991c7d-a400-7000-8000-000000000013"
SESSION_D = "01991c7d-a400-7000-8000-000000000014"


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value


def _context(data_dir: Path):
    from bizman.core import CoreContext, RepositoryAssets

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
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        manifest_sha256="c" * 64,
        evidence_sha256="d" * 64,
        started_at=started_at,
        ended_at=ended_at,
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


def _change(
    profile: str,
    suffix: str,
    first_session_id: str,
    first_seen_at: str,
) -> ChangeIndexRecord:
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
            _session(
                SESSION_A,
                started_at="2026-09-25T10:00:00Z",
                ended_at="2026-09-25T10:01:00Z",
                event_count=10,
            ),
            _session(
                SESSION_B,
                started_at="2026-09-25T10:01:00Z",
                ended_at="2026-09-25T10:02:00Z",
                event_count=20,
            ),
            _session(
                SESSION_C,
                started_at="2026-09-25T10:01:00Z",
                ended_at="2026-09-25T10:03:00Z",
                event_count=30,
            ),
        ),
        changes=(
            _change(PROFILE_A, "a", SESSION_A, "2026-09-25T10:00:30Z"),
            _change(PROFILE_A, "b", SESSION_B, "2026-09-25T10:01:30Z"),
            _change(PROFILE_B, "a", SESSION_C, "2026-09-25T10:02:30Z"),
        ),
    )


def _build_index(
    data_dir: Path,
    runtime: RuntimeProjection | None = None,
) -> None:
    rebuild_agent_index(
        data_dir / "index" / "agent-index.sqlite3",
        _knowledge_projection(),
        runtime if runtime is not None else _runtime_projection(),
        completed_at="2026-09-25T12:00:00Z",
    )


class CoreReadApiTests(unittest.TestCase):
    def test_public_read_dtos_are_frozen_slotted_path_free_and_core_owned(self):
        from bizman.core import (
            ChangeGetRequest,
            ChangeGetResult,
            ChangeListRequest,
            ChangePage,
            ChangeRecord,
            KnowledgeGetRequest,
            KnowledgeGetResult,
            KnowledgeHit,
            KnowledgeItem,
            KnowledgeResolveRequest,
            KnowledgeResolveResult,
            KnowledgeSearchRequest,
            KnowledgeSearchResult,
            SessionGetRequest,
            SessionGetResult,
            SessionListRequest,
            SessionPage,
            SessionRecord,
        )

        dto_types = (
            ChangeGetRequest,
            ChangeGetResult,
            ChangeListRequest,
            ChangePage,
            ChangeRecord,
            KnowledgeGetRequest,
            KnowledgeGetResult,
            KnowledgeHit,
            KnowledgeItem,
            KnowledgeResolveRequest,
            KnowledgeResolveResult,
            KnowledgeSearchRequest,
            KnowledgeSearchResult,
            SessionGetRequest,
            SessionGetResult,
            SessionListRequest,
            SessionPage,
            SessionRecord,
        )
        for dto in dto_types:
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dto.__dataclass_params__.frozen)
                self.assertIn("__slots__", dto.__dict__)
                self.assertEqual(dto.__module__, "bizman.core.read")
                annotations = " ".join(str(field.type) for field in fields(dto))
                self.assertNotIn("Path", annotations)
                self.assertNotIn("readmodel", annotations.casefold())

    def test_knowledge_resolve_search_and_get_are_core_owned(self):
        from bizman.core import (
            KnowledgeGetRequest,
            KnowledgeResolveRequest,
            KnowledgeSearchRequest,
            get_knowledge,
            resolve_knowledge,
            search_knowledge,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)

            resolved = resolve_knowledge(
                context,
                KnowledgeResolveRequest(text="bm.city.1"),
            )
            searched = search_knowledge(
                context,
                KnowledgeSearchRequest(text="Alpha", limit=5, kinds=("city",)),
            )
            fetched = get_knowledge(
                context,
                KnowledgeGetRequest(ref="bm.city.1"),
            )
            missing = get_knowledge(
                context,
                KnowledgeGetRequest(ref="bm.city.999"),
            )

        self.assertIsNotNone(resolved.hit)
        assert resolved.hit is not None
        self.assertEqual(resolved.hit.ref, "bm.city.1")
        self.assertEqual(resolved.hit.match_kind, "exact_ref")
        self.assertEqual([item.ref for item in searched.items], ["bm.city.1"])
        self.assertIsNotNone(fetched.item)
        assert fetched.item is not None
        self.assertEqual(fetched.item.aliases, ("Alpha",))
        self.assertEqual(fetched.item.source_dataset, "tests.synthetic")
        self.assertIsNone(missing.item)
        self.assertEqual(type(fetched.item).__module__, "bizman.core.read")
        self.assertEqual(type(resolved.hit).__module__, "bizman.core.read")

    def test_session_pagination_is_stable_and_exact_get_is_bounded(self):
        from bizman.core import (
            SessionGetRequest,
            SessionListRequest,
            get_session,
            list_sessions,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)

            first = list_sessions(context, SessionListRequest(limit=2))
            repeat = list_sessions(context, SessionListRequest(limit=2))
            self.assertEqual(first, repeat)
            self.assertIsNotNone(first.next_cursor)
            second = list_sessions(
                context,
                SessionListRequest(limit=2, cursor=first.next_cursor),
            )
            exact = get_session(context, SessionGetRequest(session_id=SESSION_B))

        self.assertEqual(
            [item.session_id for item in first.items],
            [SESSION_A, SESSION_B],
        )
        self.assertEqual([item.session_id for item in second.items], [SESSION_C])
        self.assertIsNone(second.next_cursor)
        self.assertEqual(
            len({item.session_id for item in (*first.items, *second.items)}),
            3,
        )
        self.assertIsNotNone(exact.session)
        assert exact.session is not None
        self.assertEqual(exact.session.event_count, 20)

    def test_session_cursor_rejects_semantically_changed_index_generation(self):
        from bizman.core import SessionListRequest, list_sessions

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)
            first = list_sessions(context, SessionListRequest(limit=1))
            self.assertIsNotNone(first.next_cursor)

            original = _runtime_projection()
            changed = RuntimeProjection(
                sessions=(
                    replace(original.sessions[0], event_count=11),
                    *original.sessions[1:],
                ),
                changes=original.changes,
            )
            _build_index(data_dir, changed)

            with self.assertRaisesRegex(ValueError, "generation"):
                list_sessions(
                    context,
                    SessionListRequest(limit=1, cursor=first.next_cursor),
                )

    def test_session_comparison_uses_signed_to_minus_from_deltas(self):
        from bizman.core import SessionCompareRequest, compare_sessions

        original = _runtime_projection()
        before = replace(
            original.sessions[0],
            event_count=20,
            action_count=5,
            http_request_count=10,
            http_response_count=9,
            correlation_strong_count=2,
            correlation_probable_count=1,
            correlation_temporal_count=1,
            correlation_exact_count=1,
            uncorrelated_action_count=1,
            warning_count=2,
            anomaly_count=3,
        )
        after = replace(
            original.sessions[1],
            event_count=14,
            action_count=7,
            http_request_count=8,
            http_response_count=11,
            correlation_strong_count=4,
            correlation_probable_count=0,
            correlation_temporal_count=2,
            correlation_exact_count=1,
            uncorrelated_action_count=2,
            warning_count=1,
            anomaly_count=5,
        )
        runtime = RuntimeProjection(
            sessions=(before, after, original.sessions[2]),
            changes=original.changes,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir, runtime)
            result = compare_sessions(
                _context(data_dir),
                SessionCompareRequest(
                    from_session_id=before.session_id,
                    to_session_id=after.session_id,
                ),
            )

        self.assertEqual(result.missing_session_ids, ())
        self.assertIsNotNone(result.comparison)
        assert result.comparison is not None
        comparison = result.comparison
        self.assertEqual(comparison.from_session_id, before.session_id)
        self.assertEqual(comparison.to_session_id, after.session_id)
        self.assertEqual(comparison.event_count_delta, -6)
        self.assertEqual(comparison.action_count_delta, 2)
        self.assertEqual(comparison.http_request_count_delta, -2)
        self.assertEqual(comparison.http_response_count_delta, 2)
        self.assertEqual(comparison.correlation_strong_count_delta, 2)
        self.assertEqual(comparison.correlation_probable_count_delta, -1)
        self.assertEqual(comparison.correlation_temporal_count_delta, 1)
        self.assertEqual(comparison.correlation_exact_count_delta, 0)
        self.assertEqual(comparison.uncorrelated_action_count_delta, 1)
        self.assertEqual(comparison.warning_count_delta, -1)
        self.assertEqual(comparison.anomaly_count_delta, 2)

    def test_same_session_comparison_is_zero_and_missing_side_is_explicit(self):
        from bizman.core import SessionCompareRequest, compare_sessions

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)

            same = compare_sessions(
                context,
                SessionCompareRequest(
                    from_session_id=SESSION_A,
                    to_session_id=SESSION_A,
                ),
            )
            missing = compare_sessions(
                context,
                SessionCompareRequest(
                    from_session_id=SESSION_A,
                    to_session_id=SESSION_D,
                ),
            )

        self.assertIsNotNone(same.comparison)
        assert same.comparison is not None
        delta_fields = (
            "event_count_delta",
            "action_count_delta",
            "http_request_count_delta",
            "http_response_count_delta",
            "correlation_strong_count_delta",
            "correlation_probable_count_delta",
            "correlation_temporal_count_delta",
            "correlation_exact_count_delta",
            "uncorrelated_action_count_delta",
            "warning_count_delta",
            "anomaly_count_delta",
        )
        self.assertTrue(
            all(getattr(same.comparison, field) == 0 for field in delta_fields)
        )
        self.assertIsNone(missing.comparison)
        self.assertEqual(missing.missing_session_ids, (SESSION_D,))

    def test_session_anomalies_filter_and_paginate_without_gaps(self):
        from bizman.core import (
            SessionAnomalyListRequest,
            list_session_anomalies,
        )

        base = _runtime_projection()
        zero = _session(
            SESSION_D,
            started_at="2026-09-25T10:04:00Z",
            ended_at="2026-09-25T10:05:00Z",
            event_count=40,
        )
        runtime = RuntimeProjection(
            sessions=(
                replace(base.sessions[0], warning_count=1),
                replace(base.sessions[1], anomaly_count=2),
                replace(
                    base.sessions[2],
                    action_count=2,
                    uncorrelated_action_count=1,
                ),
                zero,
            ),
            changes=base.changes,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir, runtime)
            context = _context(data_dir)

            first = list_session_anomalies(
                context,
                SessionAnomalyListRequest(limit=2),
            )
            self.assertIsNotNone(first.next_cursor)
            second = list_session_anomalies(
                context,
                SessionAnomalyListRequest(
                    limit=2,
                    cursor=first.next_cursor,
                ),
            )

        combined = (*first.items, *second.items)
        self.assertEqual(
            [item.session_id for item in combined],
            [SESSION_A, SESSION_B, SESSION_C],
        )
        self.assertEqual(len({item.session_id for item in combined}), 3)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(first.items[0].warning_count, 1)
        self.assertEqual(first.items[1].anomaly_count, 2)
        self.assertEqual(second.items[0].uncorrelated_action_count, 1)
        self.assertNotIn(SESSION_D, {item.session_id for item in combined})

    def test_session_anomaly_cursor_is_operation_scoped_and_generation_bound(self):
        from bizman.core import (
            SessionAnomalyListRequest,
            SessionListRequest,
            list_session_anomalies,
            list_sessions,
        )

        base = _runtime_projection()
        runtime = RuntimeProjection(
            sessions=tuple(
                replace(item, warning_count=1)
                for item in base.sessions
            ),
            changes=base.changes,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir, runtime)
            context = _context(data_dir)

            ordinary = list_sessions(context, SessionListRequest(limit=1))
            assert ordinary.next_cursor is not None
            with self.assertRaisesRegex(ValueError, "does not belong"):
                SessionAnomalyListRequest(
                    limit=1,
                    cursor=ordinary.next_cursor,
                )

            first = list_session_anomalies(
                context,
                SessionAnomalyListRequest(limit=1),
            )
            assert first.next_cursor is not None

            changed = RuntimeProjection(
                sessions=(
                    replace(runtime.sessions[0], event_count=999),
                    *runtime.sessions[1:],
                ),
                changes=runtime.changes,
            )
            _build_index(data_dir, changed)

            with self.assertRaisesRegex(ValueError, "generation"):
                list_session_anomalies(
                    context,
                    SessionAnomalyListRequest(
                        limit=1,
                        cursor=first.next_cursor,
                    ),
                )

    def test_session_intelligence_limits_are_bounded(self):
        from bizman.core import SessionAnomalyListRequest, SessionCompareRequest

        for invalid in (0, 51, -1, True):
            with self.subTest(limit=invalid), self.assertRaises(
                (TypeError, ValueError)
            ):
                SessionAnomalyListRequest(limit=invalid)

        with self.assertRaises(ValueError):
            SessionCompareRequest(
                from_session_id="not-a-uuid",
                to_session_id=SESSION_A,
            )

    def test_change_pagination_preserves_profile_scope(self):
        from bizman.core import ChangeListRequest, list_changes

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)

            first = list_changes(
                context,
                ChangeListRequest(limit=1, analysis_profile_sha256=PROFILE_A),
            )
            self.assertIsNotNone(first.next_cursor)
            second = list_changes(
                context,
                ChangeListRequest(
                    limit=1,
                    analysis_profile_sha256=PROFILE_A,
                    cursor=first.next_cursor,
                ),
            )
            profile_b = list_changes(
                context,
                ChangeListRequest(limit=10, analysis_profile_sha256=PROFILE_B),
            )

        self.assertEqual([item.change_id for item in first.items], ["chg.a"])
        self.assertEqual([item.change_id for item in second.items], ["chg.b"])
        self.assertIsNone(second.next_cursor)
        self.assertEqual(
            {item.analysis_profile_sha256 for item in (*first.items, *second.items)},
            {PROFILE_A},
        )
        self.assertEqual(
            {item.analysis_profile_sha256 for item in profile_b.items},
            {PROFILE_B},
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            ChangeListRequest(
                limit=1,
                analysis_profile_sha256=PROFILE_B,
                cursor=first.next_cursor,
            )

    def test_change_exact_get_uses_profile_and_change_identity(self):
        from bizman.core import ChangeGetRequest, get_change

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            context = _context(data_dir)
            found = get_change(
                context,
                ChangeGetRequest(
                    analysis_profile_sha256=PROFILE_A,
                    change_id="chg.a",
                ),
            )
            missing = get_change(
                context,
                ChangeGetRequest(
                    analysis_profile_sha256=PROFILE_B,
                    change_id="chg.b",
                ),
            )

        self.assertIsNotNone(found.change)
        assert found.change is not None
        self.assertEqual(found.change.first_session_id, SESSION_A)
        self.assertIsNone(missing.change)

    def test_cursor_integrity_and_query_limits_fail_closed(self):
        from bizman.core import (
            ChangeListRequest,
            KnowledgeSearchRequest,
            SessionListRequest,
            list_sessions,
        )

        for invalid in (0, 51, -1, True):
            with self.subTest(limit=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    SessionListRequest(limit=invalid)
                with self.assertRaises((TypeError, ValueError)):
                    KnowledgeSearchRequest(text="alpha", limit=invalid)

        with self.assertRaises(ValueError):
            KnowledgeSearchRequest(text="alpha", kinds=("not-a-kind",))

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_index(data_dir)
            page = list_sessions(_context(data_dir), SessionListRequest(limit=1))
            assert page.next_cursor is not None
            replacement = "A" if page.next_cursor[-1] != "A" else "B"
            tampered = page.next_cursor[:-1] + replacement

        with self.assertRaises(ValueError):
            SessionListRequest(limit=1, cursor=tampered)
        with self.assertRaises(ValueError):
            ChangeListRequest(limit=1, cursor=page.next_cursor)

    def test_missing_incompatible_and_corrupt_indexes_map_to_core_errors(self):
        from bizman.core import (
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            SessionListRequest,
            list_sessions,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "missing"
            with self.assertRaises(ConfigurationError):
                list_sessions(_context(data_dir), SessionListRequest())

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "foreign"
            path = data_dir / "index" / "agent-index.sqlite3"
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA application_id = 123")
                connection.commit()
            with self.assertRaises(ContractMismatchError):
                list_sessions(_context(data_dir), SessionListRequest())

        corruption_cases = (
            (
                "metadata",
                "UPDATE index_meta SET value = 'not-rfc3339' "
                "WHERE key = 'completed_at'",
            ),
            (
                "knowledge",
                "UPDATE knowledge_item SET body = 'tampered' WHERE ref = 'bm.city.1'",
            ),
            (
                "fts",
                "UPDATE knowledge_fts SET title = 'tampered' WHERE ref = 'bm.city.1'",
            ),
            (
                "foreign-key",
                "INSERT INTO alias(normalized_alias, alias, ref) "
                "VALUES ('orphan', 'orphan', 'bm.city.999')",
            ),
        )
        for label, statement in corruption_cases:
            with self.subTest(corruption=label), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp) / "corrupt"
                _build_index(data_dir)
                path = data_dir / "index" / "agent-index.sqlite3"
                with sqlite3.connect(path) as connection:
                    connection.execute(statement)
                    connection.commit()
                with self.assertRaises(DataIntegrityError):
                    list_sessions(_context(data_dir), SessionListRequest())


if __name__ == "__main__":
    unittest.main()
