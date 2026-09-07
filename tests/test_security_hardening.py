import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_foundation.redaction import load_redaction_policy, redact_mapping
from tools.bizman_foundation.validation import validate_repository


class RedactionPolicyHardeningTests(unittest.TestCase):
    def test_repository_policy_drops_camel_case_secret_fields(self):
        policy = load_redaction_policy(
            Path(__file__).resolve().parents[1] / "config/redaction-policy.json"
        )
        result = redact_mapping(
            {
                "safe": 1,
                "accessToken": "a",
                "clientSecret": "b",
                "sessionId": "c",
                "cookieValue": "d",
            },
            policy,
        )
        self.assertEqual(result, {"safe": 1})


class ForbiddenFileHardeningTests(unittest.TestCase):
    def test_validator_allows_safe_session_named_code_and_schema_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge/sources").mkdir(parents=True)
            (root / "schemas").mkdir()
            (root / "knowledge/catalog.json").write_text(
                json.dumps({"schema_version": "2.0", "datasets": []}),
                encoding="utf-8",
            )
            (root / "tools").mkdir()
            (root / "tools/session.py").write_text("SESSION = 1\n", encoding="utf-8")
            (root / "schemas/session-manifest.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                    }
                ),
                encoding="utf-8",
            )

            result = validate_repository(root)

            self.assertFalse(
                any("forbidden committed file" in error for error in result.errors),
                result.errors,
            )


if __name__ == "__main__":
    unittest.main()
