import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_detector.baseline import CuratedBaseline
from tools.bizman_detector.normalization import (
    normalize_field_name,
    normalize_key_set,
)
from tools.bizman_foundation.redaction import RedactionPolicy


REPO_ROOT = Path(__file__).resolve().parents[1]


class DetectorNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RedactionPolicy.default()

    def test_indexed_numeric_and_sensitive_names_are_normalized(self):
        self.assertEqual(normalize_field_name(" product[17] ", self.policy), "product[n]")
        self.assertEqual(normalize_field_name("selected[397]", self.policy), "selected[n]")
        self.assertEqual(normalize_field_name("12345", self.policy), "{numeric-key}")
        self.assertIsNone(normalize_field_name("clientSecret", self.policy))
        self.assertIsNone(normalize_field_name(" accessToken ", self.policy))
        self.assertEqual(
            normalize_key_set(
                ["product[1]", "product[9]", "clientSecret", "42", "42"],
                self.policy,
            ),
            ("product[n]", "{numeric-key}"),
        )


class RepositoryBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = RedactionPolicy.default()
        cls.baseline = CuratedBaseline.load(REPO_ROOT, cls.policy)

    def test_dynamic_endpoint_template_matches_one_segment_only(self):
        match = self.baseline.match_endpoint(
            "GET", "/user/check/message/r/313965"
        )
        self.assertTrue(match.matched)
        self.assertTrue(match.known_method)
        self.assertEqual(match.path_pattern, "/user/check/message/r/{message_id}")

        trailing = self.baseline.match_endpoint(
            "GET", "/user/check/message/r/313965/"
        )
        self.assertFalse(trailing.matched)

        too_deep = self.baseline.match_endpoint(
            "GET", "/user/check/message/r/313965/extra"
        )
        self.assertFalse(too_deep.matched)

    def test_numeric_query_keys_are_a_single_dynamic_class(self):
        match = self.baseline.match_endpoint("GET", "/user/check/message")
        self.assertTrue(match.matched)
        self.assertIn("{numeric-key}", match.query_keys)
        self.assertNotIn("273", match.query_keys)

    def test_sensitive_baseline_form_fields_are_not_retained(self):
        forms = self.baseline.forms_for("POST", "/user/")
        profile_forms = [item for item in forms if "currency" in item.field_names]
        self.assertTrue(profile_forms)
        for form in profile_forms:
            self.assertNotIn("password", form.field_names)
            self.assertNotIn("oldpassword", form.field_names)
            self.assertNotIn("password1", form.field_names)

    def test_operation_keys_normalize_numeric_indexes(self):
        operations = self.baseline.operations_for("POST", "/units/vendor/select/")
        self.assertGreaterEqual(len(operations), 2)
        body_keys = {key for operation in operations for key in operation.body_keys}
        self.assertIn("product[n]", body_keys)
        self.assertIn("selected[n]", body_keys)
        self.assertNotIn("product[0]", body_keys)
        self.assertNotIn("selected[397]", body_keys)

    def test_baseline_hash_is_sha256(self):
        self.assertRegex(self.baseline.sha256, r"^[0-9a-f]{64}$")


class BaselineSemanticHashTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, reverse: bool, pretty: bool) -> None:
        (root / "knowledge/http/endpoints").mkdir(parents=True)
        (root / "knowledge/http/forms").mkdir(parents=True)
        (root / "knowledge/actions").mkdir(parents=True)

        endpoint_records = [
            {
                "path_pattern": "/b/{id}",
                "methods": {"GET": 1},
                "statuses": {"200": 1},
                "query_keys": ["mode"],
            },
            {
                "path_pattern": "/a/",
                "methods": {"POST": 1},
                "statuses": {"302": 1},
                "query_keys": ["id"],
            },
        ]
        if reverse:
            endpoint_records.reverse()
        endpoint_part = {"schema_version": "1.0", "records": endpoint_records}
        endpoints_index = {
            "schema_version": "1.0",
            "record_key": "records",
            "total_records": 2,
            "parts": [{"file": "part-000.json", "records": 2}],
        }

        form_records = [
            {
                "form_id": "f1",
                "method": "POST",
                "action": "/a/?id=99",
                "fields": [
                    {"name": "safe", "type": "text", "value": "ignored"},
                    {"name": "password", "type": "password", "value": "ignored"},
                ],
            }
        ]
        forms_index = {
            "schema_version": "1.0",
            "total_records": 1,
            "parts": [{"file": "part-000.json", "records": 1, "offset": 0}],
            "list_key": "forms",
        }
        operation_index = {
            "schema_version": "1.0",
            "operations": [
                {
                    "id": "op-1",
                    "path": "/a/",
                    "query_keys": ["id"],
                    "body_keys": ["product[7]", "safe"],
                }
            ],
        }
        action_catalog = {
            "count": 1,
            "items": [
                {
                    "id": "bm.action.a",
                    "method": "POST",
                    "path": "/a/",
                    "form_fields": [
                        {"name": "safe", "seen_in": 1},
                        {"name": "clientSecret", "seen_in": 1},
                    ],
                    "query_key_sets": [["id"]],
                }
            ],
        }

        kwargs = {"ensure_ascii": False}
        if pretty:
            kwargs["indent"] = 2
        else:
            kwargs["separators"] = (",", ":")

        def dump(path: Path, value) -> None:
            path.write_text(json.dumps(value, **kwargs) + "\n", encoding="utf-8")

        dump(root / "knowledge/http/endpoints/index.json", endpoints_index)
        dump(root / "knowledge/http/endpoints/part-000.json", endpoint_part)
        dump(root / "knowledge/http/forms/index.json", forms_index)
        dump(root / "knowledge/http/forms/part-000.json", form_records)
        dump(root / "knowledge/http/operation-index.json", operation_index)
        dump(root / "knowledge/actions/catalog.json", action_catalog)

    def test_semantically_equivalent_baselines_hash_identically(self):
        policy = RedactionPolicy.default()
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp)
            second = Path(second_tmp)
            self._write_fixture(first, reverse=False, pretty=True)
            self._write_fixture(second, reverse=True, pretty=False)

            first_baseline = CuratedBaseline.load(first, policy)
            second_baseline = CuratedBaseline.load(second, policy)

            self.assertEqual(first_baseline.sha256, second_baseline.sha256)
            self.assertEqual(
                first_baseline.forms_for("POST", "/a/")[0].field_names,
                ("safe",),
            )
            self.assertEqual(
                first_baseline.operations_for("POST", "/a/")[0].body_keys,
                ("product[n]", "safe"),
            )


if __name__ == "__main__":
    unittest.main()
