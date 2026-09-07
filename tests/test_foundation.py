import json
import re
import tempfile
import unittest
from pathlib import Path

from tools.bizman_foundation.fingerprint import canonical_sha256
from tools.bizman_foundation.redaction import (
    RedactionPolicy,
    load_redaction_policy,
    redact_headers,
    redact_mapping,
)
from tools.bizman_foundation.session import new_session_manifest
from tools.bizman_foundation.validation import validate_repository

UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class FingerprintTests(unittest.TestCase):
    def test_canonical_sha256_ignores_object_key_order(self):
        left = {"b": 2, "a": [1, {"z": True, "x": None}]}
        right = {"a": [1, {"x": None, "z": True}], "b": 2}
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))

    def test_canonical_sha256_changes_when_value_changes(self):
        self.assertNotEqual(canonical_sha256({"a": 1}), canonical_sha256({"a": 2}))


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.policy = RedactionPolicy.default()

    def test_sensitive_headers_are_dropped_case_insensitively(self):
        result = redact_headers(
            {
                "Authorization": "Bearer secret",
                "cookie": "sid=secret",
                "Accept": "application/json",
            },
            self.policy,
        )
        self.assertEqual(result, {"Accept": "application/json"})

    def test_sensitive_fields_are_dropped_recursively(self):
        result = redact_mapping(
            {
                "user": "alice",
                "password": "secret",
                "nested": {"csrfToken": "abc", "count": 2},
            },
            self.policy,
        )
        self.assertEqual(result, {"user": "alice", "nested": {"count": 2}})

    def test_policy_can_be_loaded_from_json_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "headers": {"drop": ["Authorization", "Cookie"]},
                        "fields": {"drop_patterns": ["(?i)secret"]},
                        "bodies": {
                            "first_party_only": True,
                            "max_request_bytes": 1048576,
                            "max_response_bytes": 2097152,
                        },
                    }
                ),
                encoding="utf-8",
            )
            policy = load_redaction_policy(path)
            self.assertIn("authorization", policy.drop_headers)
            self.assertTrue(policy.should_drop_field("clientSecret"))
            self.assertTrue(policy.first_party_only)
            self.assertEqual(policy.max_request_bytes, 1048576)
            self.assertEqual(policy.max_response_bytes, 2097152)


class SessionTests(unittest.TestCase):
    def test_new_session_manifest_uses_uuid7_and_records_versions(self):
        manifest = new_session_manifest(
            collector_version="0.1.0",
            browser_product="Chrome",
            browser_version="140.0.0.0",
            protocol_version="1.3",
        )
        self.assertRegex(manifest["session_id"], UUID7_RE)
        self.assertEqual(manifest["schema_version"], "1.0")
        self.assertEqual(manifest["collector"]["version"], "0.1.0")
        self.assertEqual(manifest["browser"]["product"], "Chrome")
        self.assertEqual(manifest["protocol"]["version"], "1.3")


class ValidatorV2Tests(unittest.TestCase):
    @staticmethod
    def _minimal_root(root: Path) -> None:
        (root / "knowledge/sources").mkdir(parents=True)
        (root / "schemas").mkdir()
        (root / "knowledge/catalog.json").write_text(
            json.dumps({"schema_version": "2.0", "datasets": []}), encoding="utf-8"
        )

    def test_validator_derives_counts_from_manifests_not_snapshot_constants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge/items").mkdir(parents=True)
            (root / "knowledge/sources").mkdir(parents=True)
            (root / "schemas").mkdir()
            (root / "knowledge/items/part-000.jsonl").write_text(
                '{"id":"a"}\n{"id":"b"}\n', encoding="utf-8"
            )
            (root / "knowledge/items/index.json").write_text(
                json.dumps(
                    {
                        "total_records": 2,
                        "parts": [{"file": "part-000.jsonl", "records": 2, "offset": 0}],
                    }
                ),
                encoding="utf-8",
            )
            (root / "knowledge/catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "datasets": [
                            {
                                "id": "items",
                                "path": "knowledge/items/index.json",
                                "format": "partition-manifest/jsonl",
                                "records": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertEqual(result.errors, [])

    def test_validator_rejects_manifest_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge/items").mkdir(parents=True)
            (root / "knowledge/sources").mkdir(parents=True)
            (root / "schemas").mkdir()
            (root / "knowledge/items/part-000.jsonl").write_text(
                '{"id":"a"}\n', encoding="utf-8"
            )
            (root / "knowledge/items/index.json").write_text(
                json.dumps(
                    {
                        "total_records": 2,
                        "parts": [{"file": "part-000.jsonl", "records": 2, "offset": 0}],
                    }
                ),
                encoding="utf-8",
            )
            (root / "knowledge/catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "datasets": [
                            {
                                "id": "items",
                                "path": "knowledge/items/index.json",
                                "format": "partition-manifest/jsonl",
                                "records": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(any("declares 2, contains 1" in error for error in result.errors))

    def test_validator_rejects_noncanonical_capture_source_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._minimal_root(root)
            (root / "knowledge/sources/a.har.json").write_text(
                json.dumps(
                    {"id": "src.har.bizmania.2026-09-07.01", "source_filename": "a.har"}
                ),
                encoding="utf-8",
            )
            (root / "knowledge/sources/captures.json").write_text(
                json.dumps(
                    {
                        "captures": [
                            {
                                "source_id": "har:a.har",
                                "file_name": "a.har",
                                "legacy_source_ids": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(any("source_id" in error for error in result.errors))

    def test_validator_rejects_invalid_json_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._minimal_root(root)
            (root / "schemas/bad.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": 123,
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(any("invalid JSON Schema" in error for error in result.errors))

    def test_validator_rejects_operational_database_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._minimal_root(root)
            (root / "state.db").write_bytes(b"not-a-real-db")
            result = validate_repository(root)
            self.assertTrue(any("forbidden committed file" in error for error in result.errors))

    def test_validator_validates_source_manifests_against_source_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._minimal_root(root)
            (root / "schemas/source.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "required": ["id", "source_filename", "sha256"],
                        "properties": {
                            "id": {"type": "string"},
                            "source_filename": {"type": "string"},
                            "sha256": {"type": "string"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "knowledge/sources/a.har.json").write_text(
                json.dumps(
                    {"id": "src.har.bizmania.2026-09-07.01", "source_filename": "a.har"}
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(
                any("source.schema.json" in error and "sha256" in error for error in result.errors)
            )


if __name__ == "__main__":
    unittest.main()
