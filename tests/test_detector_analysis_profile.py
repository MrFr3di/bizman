from __future__ import annotations

from dataclasses import replace
import unittest

from tools.bizman_detector.baseline import BaselineCompilation, build_analysis_profile
from tools.bizman_detector.model import RuleDescriptor, RuntimeContract
from tools.bizman_detector.rules import RULE_DESCRIPTORS


BASELINE_A = "a" * 64
BASELINE_B = "b" * 64
REDACTION_A = "c" * 64
REDACTION_B = "d" * 64


def _compilation(
    *,
    baseline_sha256: str = BASELINE_A,
    redaction_policy_sha256: str = REDACTION_A,
    contract_schema_version: int = 1,
    normalization_version: int = 1,
) -> BaselineCompilation:
    return BaselineCompilation(
        contract=RuntimeContract(
            contract_schema_version=contract_schema_version,
            normalization_version=normalization_version,
            endpoints=(),
            forms=(),
            operations=(),
            actions=(),
        ),
        baseline_sha256=baseline_sha256,
        redaction_policy_sha256=redaction_policy_sha256,
    )


class AnalysisProfileIdentityTests(unittest.TestCase):
    def test_profile_changes_for_every_interpretation_dimension(self):
        rules = (
            RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),
            RuleDescriptor("BM-FORM-001", 1, "form.signature_new"),
        )
        base = build_analysis_profile(_compilation(), rules, extraction_version=1)

        cases = {
            "baseline": build_analysis_profile(
                _compilation(baseline_sha256=BASELINE_B), rules, extraction_version=1
            ),
            "contract_schema": build_analysis_profile(
                _compilation(contract_schema_version=2), rules, extraction_version=1
            ),
            "normalization": build_analysis_profile(
                _compilation(normalization_version=2), rules, extraction_version=1
            ),
            "extraction": build_analysis_profile(
                _compilation(), rules, extraction_version=2
            ),
            "redaction": build_analysis_profile(
                _compilation(redaction_policy_sha256=REDACTION_B),
                rules,
                extraction_version=1,
            ),
            "rule_version": build_analysis_profile(
                _compilation(),
                (replace(rules[0], version=2), rules[1]),
                extraction_version=1,
            ),
        }

        for dimension, profile in cases.items():
            with self.subTest(dimension=dimension):
                self.assertNotEqual(base.sha256, profile.sha256)

    def test_rule_input_order_does_not_change_profile_identity(self):
        first = RuleDescriptor("BM-HTTP-002", 1, "endpoint.method_added")
        second = RuleDescriptor("BM-HTTP-001", 1, "endpoint.new")

        left = build_analysis_profile(
            _compilation(), (first, second), extraction_version=1
        )
        right = build_analysis_profile(
            _compilation(), (second, first), extraction_version=1
        )

        self.assertEqual(left.rules, (second, first))
        self.assertEqual(left, right)
        self.assertRegex(left.sha256, r"^[0-9a-f]{64}$")

    def test_duplicate_rule_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate rule id"):
            build_analysis_profile(
                _compilation(),
                (
                    RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),
                    RuleDescriptor("BM-HTTP-001", 2, "endpoint.new"),
                ),
                extraction_version=1,
            )

    def test_nonpositive_rule_version_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "rule version must be positive"):
            build_analysis_profile(
                _compilation(),
                (RuleDescriptor("BM-HTTP-001", 0, "endpoint.new"),),
                extraction_version=1,
            )

    def test_invalid_semantic_versions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "extraction_version must be positive"):
            build_analysis_profile(
                _compilation(),
                (RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),),
                extraction_version=0,
            )

    def test_profile_exposes_all_checkpoint_dimensions(self):
        profile = build_analysis_profile(
            _compilation(),
            (RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),),
            extraction_version=3,
        )

        self.assertEqual(profile.baseline_sha256, BASELINE_A)
        self.assertEqual(profile.contract_schema_version, 1)
        self.assertEqual(profile.normalization_version, 1)
        self.assertEqual(profile.extraction_version, 3)
        self.assertEqual(profile.redaction_policy_sha256, REDACTION_A)


class RuleRegistryTests(unittest.TestCase):
    def test_v1_registry_has_stable_unique_metadata_only_descriptors(self):
        expected = {
            "BM-HTTP-001": "endpoint.new",
            "BM-HTTP-002": "endpoint.method_added",
            "BM-HTTP-003": "endpoint.query_key_added",
            "BM-HTTP-004": "endpoint.status_added",
            "BM-FORM-001": "form.signature_new",
            "BM-FORM-002": "form.field_added",
            "BM-OP-001": "operation.new_signature",
            "BM-OP-002": "operation.query_key_added",
            "BM-OP-003": "operation.body_key_added",
            "BM-REL-001": "action_http.request_family_new",
            "BM-REL-002": "action_http.path_conflict",
        }

        self.assertEqual(
            {descriptor.rule_id: descriptor.kind for descriptor in RULE_DESCRIPTORS},
            expected,
        )
        self.assertTrue(all(descriptor.version == 1 for descriptor in RULE_DESCRIPTORS))
        self.assertEqual(
            len({descriptor.rule_id for descriptor in RULE_DESCRIPTORS}),
            len(RULE_DESCRIPTORS),
        )


if __name__ == "__main__":
    unittest.main()
