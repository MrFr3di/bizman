from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from tools.evaluations.mcp_p3 import (
    COMPACT_BYTES,
    EXPECTED_TOOLS,
    STANDARD_BYTES,
    evaluate_mcp_p3,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class MCPCompletionEvaluationTests(unittest.TestCase):
    def test_in_process_completion_gate_matches_p2_baseline_and_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = asyncio.run(
                evaluate_mcp_p3(
                    REPO_ROOT,
                    Path(tmp) / "BizManData",
                    include_stdio=False,
                )
            )

        self.assertEqual(report["evaluation_version"], 1)
        self.assertEqual(report["knowledge_records"], 590)
        self.assertEqual(
            report["runtime_fixture"],
            {"sessions": 20, "changes": 40},
        )

        retrieval = report["retrieval"]
        self.assertTrue(retrieval["passed"])
        for corpus in retrieval["corpora"].values():
            for metric in (
                "recall_at_1",
                "recall_at_5",
                "mrr",
                "evidence_correctness",
                "no_match_accuracy",
            ):
                value = corpus[metric]
                if value is not None:
                    self.assertEqual(value, 1.0)

        action_trace = report["action_trace"]
        self.assertTrue(action_trace["passed"])
        self.assertEqual(action_trace["actions"], 11)
        self.assertLessEqual(action_trace["max_calls"], 2)
        self.assertTrue(
            all(
                item["resolved"]
                and item["traced"]
                and item["calls"] <= 2
                for item in action_trace["results"]
            )
        )

        common = report["common_tasks"]
        self.assertTrue(common["passed"])
        self.assertLessEqual(
            common["median_calls_evidence_session"],
            3,
        )

        surface = report["surface"]
        self.assertTrue(surface["passed"])
        self.assertEqual(surface["tool_count"], 14)
        self.assertEqual(set(surface["tools"]), EXPECTED_TOOLS)
        self.assertTrue(surface["schemas_explicit"])
        self.assertTrue(surface["annotations_read_only_closed_world"])
        self.assertEqual(surface["forbidden_input_properties"], {})
        self.assertTrue(surface["all_output_arrays_bounded"])
        self.assertTrue(surface["no_silent_truncation"])
        self.assertLessEqual(surface["compact_max_bytes"], COMPACT_BYTES)
        self.assertLessEqual(surface["standard_max_bytes"], STANDARD_BYTES)

        self.assertTrue(report["errors"]["passed"])
        self.assertFalse(report["stdio"]["checked"])
        self.assertIsNone(report["stdio"]["passed"])
        self.assertIsNone(report["acceptance"]["stdio_protocol_clean"])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
