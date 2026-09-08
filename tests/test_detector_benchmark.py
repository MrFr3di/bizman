from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.baseline import BaselineCompiler
from tools.bizman_foundation.redaction import load_redaction_policy


class DetectorBenchmarkContractTests(unittest.TestCase):
    def test_matching_variants_are_semantically_equivalent(self):
        from tools.benchmarks.detector_stream import benchmark_matching

        repo_root = Path(__file__).resolve().parents[1]
        redaction = load_redaction_policy(repo_root / "config" / "redaction-policy.json")
        compilation = BaselineCompiler.compile(repo_root, redaction)
        patterns = tuple(endpoint.path_pattern for endpoint in compilation.contract.endpoints)

        result = benchmark_matching(patterns, requests=2_000, repeats=1)
        self.assertEqual(
            set(result["variants"]),
            {"A_linear", "B_bucketed_current", "C_segment_trie"},
        )
        counts = {item["matched"] for item in result["variants"].values()}
        self.assertEqual(len(counts), 1)
        self.assertEqual(result["semantic_equivalence"], True)
        self.assertIn(result["recommendation"], {"keep_B", "consider_C"})
        self.assertGreater(result["variants"]["B_bucketed_current"]["requests_per_second"], 0)

    def test_streaming_smoke_uses_validated_reader_and_extractor(self):
        from tools.benchmarks.detector_stream import benchmark_streaming

        repo_root = Path(__file__).resolve().parents[1]
        redaction = load_redaction_policy(repo_root / "config" / "redaction-policy.json")
        with tempfile.TemporaryDirectory() as tmp:
            result = benchmark_streaming(
                repo_root,
                redaction,
                events=200,
                work_root=Path(tmp),
            )

        self.assertEqual(result["source_events"], 200)
        self.assertEqual(result["validated_event_passes"], 3)
        self.assertEqual(result["observations"]["http"], 80)
        self.assertEqual(result["observations"]["forms"], 20)
        self.assertEqual(result["observations"]["relations"], 20)
        self.assertGreater(result["source_events_per_second"], 0)
        self.assertGreater(result["validation_events_per_second"], 0)
        self.assertGreater(result["peak_tracemalloc_bytes"], 0)
        self.assertGreater(result["event_file_bytes"], 0)

    def test_streaming_event_count_must_preserve_mix_boundary(self):
        from tools.benchmarks.detector_stream import benchmark_streaming

        repo_root = Path(__file__).resolve().parents[1]
        redaction = load_redaction_policy(repo_root / "config" / "redaction-policy.json")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "multiple of 20"):
                benchmark_streaming(
                    repo_root,
                    redaction,
                    events=21,
                    work_root=Path(tmp),
                )


if __name__ == "__main__":
    unittest.main()
