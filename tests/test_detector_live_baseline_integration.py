from __future__ import annotations

from pathlib import Path
import unittest

from tools.bizman_detector.baseline import BaselineCompiler
from tools.bizman_foundation.redaction import RedactionPolicy


REPO_ROOT = Path(__file__).resolve().parents[1]


class LiveOperationBaselineIntegrationTests(unittest.TestCase):
    def test_every_accepted_operation_is_represented_by_http_contract(self) -> None:
        contract = BaselineCompiler.compile(
            REPO_ROOT,
            RedactionPolicy.default(),
        ).contract

        endpoints = {
            (endpoint.path_pattern, method.method): method
            for endpoint in contract.endpoints
            for method in endpoint.methods
        }
        for operation in contract.operations:
            with self.subTest(path=operation.path_pattern, method=operation.method):
                method = endpoints[(operation.path_pattern, operation.method)]
                variants = {(variant.query_keys, variant.status) for variant in method.variants}
                expected = {
                    (operation.query_keys, status)
                    for status in operation.statuses
                }
                self.assertTrue(expected.issubset(variants))


if __name__ == "__main__":
    unittest.main()
