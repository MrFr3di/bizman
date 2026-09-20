from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.readmodel import (
    EvaluationCase,
    KnowledgeIndex,
    MatchKind,
    SearchQuery,
    evaluate_retrieval,
    project_curated_knowledge,
    rebuild_knowledge_index,
)
from bizman.readmodel.store import (
    INDEX_APPLICATION_ID,
    INDEX_SCHEMA_VERSION,
    ReadModelCompatibilityError,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_COMPLETED_AT = "2026-09-20T13:30:00Z"


class KnowledgeProjectionTests(unittest.TestCase):
    def test_projection_has_expected_initial_curated_scope(self):
        projection = project_curated_knowledge(REPO_ROOT)
        self.assertEqual(len(projection.records), 333)
        counts: dict[str, int] = {}
        for record in projection.records:
            counts[record.kind.value] = counts.get(record.kind.value, 0) + 1
            self.assertTrue(record.evidence_refs, record.ref)
        self.assertEqual(
            counts,
            {
                "action": 11,
                "city": 2,
                "company": 1,
                "product": 303,
                "unit": 16,
            },
        )
        self.assertRegex(projection.source_fingerprint, r"^[0-9a-f]{64}$")


class KnowledgeIndexTests(unittest.TestCase):
    def _build(self, root: Path) -> tuple[Path, str]:
        projection = project_curated_knowledge(REPO_ROOT)
        path = root / "BizManData" / "index" / "agent-index.sqlite3"
        generation = rebuild_knowledge_index(
            path,
            projection,
            completed_at=FIXED_COMPLETED_AT,
        )
        return path, generation

    def test_rebuild_is_deterministic_and_identified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path, first_generation = self._build(root / "first")
            second_path, second_generation = self._build(root / "second")
            self.assertEqual(first_generation, second_generation)

            for path in (first_path, second_path):
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(
                        connection.execute("PRAGMA application_id").fetchone()[0],
                        INDEX_APPLICATION_ID,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        INDEX_SCHEMA_VERSION,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM ref").fetchone()[0],
                        333,
                    )

            with KnowledgeIndex(first_path) as index:
                meta = index.metadata()
            self.assertEqual(meta["generation"], first_generation)
            self.assertEqual(meta["item_count"], "333")
            self.assertEqual(meta["completed_at"], FIXED_COMPLETED_AT)

    def test_foreign_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foreign.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE foreign_table(value TEXT)")
            with self.assertRaises(ReadModelCompatibilityError):
                KnowledgeIndex(path)

    def test_search_resolution_order_and_bounded_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._build(Path(tmp))
            with KnowledgeIndex(path) as index:
                exact = index.search(SearchQuery("bm.product.carseat", limit=5))
                self.assertEqual(exact[0].ref, "bm.product.carseat")
                self.assertEqual(exact[0].match_kind, MatchKind.EXACT_REF)

                alias = index.search(SearchQuery("product:416", limit=5))
                self.assertEqual(alias[0].ref, "bm.product.antiseptic")
                self.assertEqual(alias[0].match_kind, MatchKind.EXACT_ALIAS)

                title = index.search(SearchQuery("Компания Paradise", limit=5))
                self.assertEqual(title[0].ref, "bm.company.13393")
                self.assertEqual(title[0].match_kind, MatchKind.EXACT_TITLE)

                full_text = index.search(SearchQuery("магазинов электроники", limit=5))
                self.assertEqual(full_text[0].ref, "bm.unit.13443")
                self.assertEqual(full_text[0].match_kind, MatchKind.FULL_TEXT)
                self.assertLessEqual(len(full_text[0].evidence_refs), 8)

    def test_search_budget_is_hard_bounded(self):
        with self.assertRaises(ValueError):
            SearchQuery("product", limit=0)
        with self.assertRaises(ValueError):
            SearchQuery("product", limit=51)
        with self.assertRaises(ValueError):
            SearchQuery("   ")


class RetrievalEvaluationTests(unittest.TestCase):
    def test_v1_curated_eval_is_perfect_and_evidence_correct(self):
        document = json.loads(
            (REPO_ROOT / "tests/fixtures/retrieval_eval_v1.json").read_text(
                encoding="utf-8"
            )
        )
        cases = tuple(EvaluationCase(**case) for case in document["cases"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-index.sqlite3"
            rebuild_knowledge_index(
                path,
                project_curated_knowledge(REPO_ROOT),
                completed_at=FIXED_COMPLETED_AT,
            )
            with KnowledgeIndex(path) as index:
                metrics = evaluate_retrieval(index, cases)

        self.assertEqual(metrics.cases, len(cases))
        self.assertEqual(metrics.recall_at_1, 1.0)
        self.assertEqual(metrics.recall_at_5, 1.0)
        self.assertEqual(metrics.mrr, 1.0)
        self.assertEqual(metrics.evidence_correctness, 1.0)


if __name__ == "__main__":
    unittest.main()