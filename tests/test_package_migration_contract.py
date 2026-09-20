from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import unittest

from tools.bizman_detector.baseline import BaselineCompiler, build_analysis_profile
from tools.bizman_detector.rules import RULE_DESCRIPTORS
from tools.bizman_foundation.fingerprint import canonical_sha256
from tools.bizman_foundation.redaction import load_redaction_policy


REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "package_migration_golden.json"
REGRESSION_MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "pre_migration_regression_blobs.json"
)


def _semantic_fingerprint() -> dict[str, object]:
    redaction = load_redaction_policy(REPO_ROOT / "config" / "redaction-policy.json")
    compilation = BaselineCompiler.compile(REPO_ROOT, redaction)
    profile = build_analysis_profile(compilation, RULE_DESCRIPTORS)
    contract = compilation.contract
    return {
        "analysis_profile_sha256": profile.sha256,
        "baseline_sha256": compilation.baseline_sha256,
        "contract_sha256": canonical_sha256(asdict(contract)),
        "redaction_policy_sha256": compilation.redaction_policy_sha256,
        "counts": {
            "actions": len(contract.actions),
            "endpoints": len(contract.endpoints),
            "forms": len(contract.forms),
            "operations": len(contract.operations),
        },
        "versions": {
            "contract_schema": profile.contract_schema_version,
            "extraction": profile.extraction_version,
            "normalization": profile.normalization_version,
        },
        "rules": [
            {"rule_id": item.rule_id, "rule_version": item.version}
            for item in RULE_DESCRIPTORS
        ],
    }


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


class PackageMigrationSemanticContractTests(unittest.TestCase):
    def test_current_semantic_fingerprint_matches_pre_migration_golden(self):
        actual = _semantic_fingerprint()
        if not GOLDEN_PATH.exists():
            self.fail(
                "pre-migration golden is not captured; reviewed actual fingerprint: "
                + json.dumps(actual, ensure_ascii=False, sort_keys=True)
            )
        expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)


    def test_selected_pre_migration_regressions_remain_byte_identical(self):
        manifest = json.loads(REGRESSION_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["base_commit"],
            "c96d96ecfe0cab7fa4d115c1cf757ab5741f28b2",
        )
        for relative, expected_blob_sha in sorted(manifest["files"].items()):
            path = REPO_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(_git_blob_sha(path), expected_blob_sha, relative)


if __name__ == "__main__":
    unittest.main()