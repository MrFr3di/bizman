from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.benchmarks.readmodel_core import benchmark_readmodel_core, synthetic_runtime


REPO_ROOT = Path(__file__).resolve().parents[1]


class ReadModelBenchmarkContractTests(unittest.TestCase):
    def test_synthetic_runtime_is_deterministic_and_profile_scoped(self):
        first = synthetic_runtime(sessions=3, changes=6)
        second = synthetic_runtime(sessions=3, changes=6)

        self.assertEqual(first, second)
        self.assertEqual(first.source_fingerprint, second.source_fingerprint)
        self.assertEqual(len(first.sessions), 3)
        self.assertEqual(len(first.changes), 6)
        self.assertEqual(
            len({item.analysis_profile_sha256 for item in first.changes}),
            2,
        )

    def test_readmodel_benchmark_smoke_reports_required_p2e_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = benchmark_readmodel_core(
                REPO_ROOT,
                repeats=1,
                small_sessions=2,
                small_changes=2,
                large_sessions=3,
                large_changes=4,
                work_root=Path(tmp),
            )

        self.assertEqual(result["benchmark_version"], 1)
        self.assertEqual(
            result["evaluation"]["decision"],
            "keep_lexical_baseline",
        )
        self.assertFalse(result["evaluation"]["lexical_gap_detected"])
        self.assertEqual(
            result["evaluation"]["corpora"]["v3"]["overall_accuracy"],
            1.0,
        )

        cold = result["latency"]["cold_core"]
        warm = result["latency"]["warm_index"]
        for operation in (
            "resolve_exact_ref",
            "resolve_exact_alias",
            "search_fts",
            "knowledge_get",
            "sessions_page_20",
            "session_get",
            "changes_page_20",
            "changes_profile_page_20",
            "change_get",
        ):
            self.assertIn(operation, cold)
            self.assertIn(operation, warm)
            self.assertGreaterEqual(cold[operation]["p50_ms"], 0)
            self.assertGreaterEqual(warm[operation]["p50_ms"], 0)

        self.assertGreater(
            result["serialized_result_bytes"]["knowledge_get"],
            0,
        )
        self.assertEqual(
            set(result["rebuild"]),
            {"curated_only", "small_runtime", "large_runtime"},
        )
        for rebuild in result["rebuild"].values():
            self.assertEqual(rebuild["item_count"], 590)
            self.assertGreater(rebuild["database_bytes"], 0)
            self.assertRegex(rebuild["generation"], r"^[0-9a-f]{64}$")

        self.assertIn("fts", result["query_plans"])
        self.assertTrue(result["query_plans"]["fts"])

    def test_invalid_synthetic_runtime_sizes_fail_closed(self):
        with self.assertRaises(ValueError):
            synthetic_runtime(sessions=0, changes=1)
        with self.assertRaises(ValueError):
            synthetic_runtime(sessions=1, changes=-1)


if __name__ == "__main__":
    unittest.main()
