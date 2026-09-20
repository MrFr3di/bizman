from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from bizman.readmodel import (
    EvaluationCase,
    KnowledgeIndex,
    KnowledgeProjection,
    KnowledgeRecord,
    MatchKind,
    RefKind,
    SearchQuery,
    evaluate_retrieval,
    project_curated_knowledge,
    rebuild_knowledge_index,
)
from bizman.readmodel.store import (
    INDEX_APPLICATION_ID,
    INDEX_SCHEMA_VERSION,
    ReadModelCompatibilityError,
    ReadModelIntegrityError,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_COMPLETED_AT = "2026-09-20T13:30:00Z"


class KnowledgeProjectionTests(unittest.TestCase):
    def _copy_curated_scope(self, root: Path) -> Path:
        (root / "knowledge/actions").mkdir(parents=True)
        (root / "knowledge/domain").mkdir(parents=True)
        shutil.copy2(
            REPO_ROOT / "knowledge/actions/catalog.json",
            root / "knowledge/actions/catalog.json",
        )
        shutil.copy2(
            REPO_ROOT / "knowledge/domain/entities.json",
            root / "knowledge/domain/entities.json",
        )
        shutil.copytree(
            REPO_ROOT / "knowledge/domain/products",
            root / "knowledge/domain/products",
        )
        return root

    def test_projection_has_expected_initial_curated_scope(self):
        projection = project_curated_knowledge(REPO_ROOT)
        self.assertEqual(len(projection.records), 590)
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
                "endpoint": 68,
                "operation": 15,
                "form": 87,
                "wiki_topic": 87,
            },
        )
        self.assertRegex(projection.source_fingerprint, r"^[0-9a-f]{64}$")



    def test_extended_projectors_keep_values_out_of_search_text(self):
        projection = project_curated_knowledge(REPO_ROOT)
        records = {record.ref: record for record in projection.records}

        operation = records["bm.operation.v1.ef4587de37e586d824803748"]
        self.assertIn("vendor", operation.body)
        self.assertNotIn("8561", operation.body)
        self.assertIn(
            "src.har.bizmania.2026-09-06.02#entry-296",
            operation.evidence_refs,
        )

        form = records["bm.form.v1.149acbb9964adf9f"]
        self.assertIn("city", form.body)
        self.assertNotIn("25", form.body)
        self.assertIn(
            "src.har.bizmania.2026-09-06.01#entry-10125",
            form.evidence_refs,
        )

        endpoint = records["bm.endpoint.v1.a79459f9202f98dc9a50155f"]
        self.assertIn("cmd", endpoint.body)
        self.assertNotIn("25", endpoint.body)
        self.assertIn(
            "src.har.bizmania.2026-09-06.01#entry-7822",
            endpoint.evidence_refs,
        )

        wiki = records["bm.wiki.v1.b3142a5c857c9885c7d64247"]
        self.assertEqual(wiki.title, "Авторегулирование снабжения")
        self.assertIn("автозакупка", wiki.body.casefold())
        self.assertEqual(
            wiki.evidence_refs,
            ("src.har.bizmania-faq.2026-09-06.01#entry-2296",),
        )

    def test_projection_rejects_tampered_source_fingerprint(self):
        record = KnowledgeRecord(
            ref="bm.product.synthetic",
            kind=RefKind.PRODUCT,
            title="Synthetic",
            aliases=("synthetic",),
            body="synthetic",
            evidence_refs=("src.synthetic#1",),
            source_dataset="products",
        )
        with self.assertRaisesRegex(ValueError, "source_fingerprint"):
            KnowledgeProjection(records=(record,), source_fingerprint="0" * 64)

    def test_projection_fails_closed_on_manifest_or_confidence_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy_curated_scope(Path(tmp) / "repo")
            action_path = root / "knowledge/actions/catalog.json"
            actions = json.loads(action_path.read_text(encoding="utf-8"))
            actions["items"][0]["confidence"] = "inferred"
            action_path.write_text(json.dumps(actions), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confidence"):
                project_curated_knowledge(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy_curated_scope(Path(tmp) / "repo")
            index_path = root / "knowledge/domain/products/index.json"
            product_index = json.loads(index_path.read_text(encoding="utf-8"))
            product_index["parts"][1]["offset"] += 1
            index_path.write_text(json.dumps(product_index), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "offset"):
                project_curated_knowledge(root)

    def test_record_kind_must_match_canonical_ref_namespace(self):
        with self.assertRaisesRegex(ValueError, "does not match kind"):
            KnowledgeRecord(
                ref="bm.action.synthetic",
                kind=RefKind.PRODUCT,
                title="Synthetic",
                aliases=(),
                body="",
                evidence_refs=("src.synthetic#1",),
                source_dataset="products",
            )



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
                        590,
                    )

            with KnowledgeIndex(first_path) as index:
                meta = index.metadata()
            self.assertEqual(meta["generation"], first_generation)
            self.assertEqual(meta["item_count"], "590")
            self.assertEqual(meta["completed_at"], FIXED_COMPLETED_AT)

    def test_foreign_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foreign.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE foreign_table(value TEXT)")
            with self.assertRaises(ReadModelCompatibilityError):
                KnowledgeIndex(path)


    def test_spoofed_identity_without_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spoofed.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"PRAGMA application_id = {INDEX_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
                connection.execute("CREATE TABLE decoy(value TEXT) STRICT")
            with self.assertRaisesRegex(ReadModelCompatibilityError, "missing required tables"):
                KnowledgeIndex(path)

    def test_tampered_metadata_or_item_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._build(Path(tmp))
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE index_meta SET value = ? WHERE key = 'generation'",
                    ("0" * 64,),
                )
                connection.commit()
            with self.assertRaisesRegex(ReadModelIntegrityError, "generation fingerprint"):
                KnowledgeIndex(path)

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._build(Path(tmp))
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE index_meta SET value = ? WHERE key = 'item_count'",
                    ("999",),
                )
                connection.commit()
            with self.assertRaisesRegex(ReadModelIntegrityError, "item counts"):
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
    def _assert_eval_fixture_is_perfect(self, fixture: str) -> None:
        document = json.loads(
            (REPO_ROOT / "tests/fixtures" / fixture).read_text(encoding="utf-8")
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

    def test_v1_curated_eval_remains_perfect(self):
        self._assert_eval_fixture_is_perfect("retrieval_eval_v1.json")

    def test_v2_extended_eval_is_perfect(self):
        self._assert_eval_fixture_is_perfect("retrieval_eval_v2.json")


if __name__ == "__main__":
    unittest.main()