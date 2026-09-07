from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.baseline import (
    BaselineCompiler,
    BaselineConsistencyError,
    BaselineFormatError,
)
from tools.bizman_foundation.redaction import RedactionPolicy


REPO_ROOT = Path(__file__).resolve().parents[1]


def _dump(path: Path, value: object, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    path.write_text(json.dumps(value, **kwargs) + "\n", encoding="utf-8")


def _write_fixture(
    root: Path,
    *,
    reverse: bool = False,
    split_endpoint_parts: bool = False,
) -> None:
    endpoint_records = [
        {
            "path_pattern": "/x",
            "count": 2,
            "methods": {"GET": 1, "POST": 1},
            "statuses": {"200": 1, "302": 1},
            "query_keys": ["getMode", "postMode"],
        },
        {
            "path_pattern": "/items/{item_id}",
            "count": 1,
            "methods": {"GET": 1},
            "statuses": {"200": 1},
            "query_keys": [],
        },
    ]
    application_events = [
        {
            "capture": "fixture.har",
            "entry": 1,
            "method": "GET",
            "path": "/x",
            "query": {"getMode": "ignored"},
            "status": 200,
        },
        {
            "capture": "fixture.har",
            "entry": 2,
            "method": "POST",
            "path": "/x",
            "query": {"postMode": "ignored"},
            "status": 302,
        },
        {
            "capture": "fixture.har",
            "entry": 3,
            "method": "GET",
            "path": "/items/123",
            "query": {},
            "status": 200,
        },
    ]
    forms = [
        {
            "form_id": "f1",
            "method": "POST",
            "action": "/x?mode=ignored",
            "fields": [
                {"name": "safe", "type": "text", "value": "DO_NOT_KEEP"},
                {"name": "product[7]", "type": "text", "value": "42"},
                {"name": "clientSecret", "type": "hidden", "value": "TOP_SECRET"},
            ],
            "observed_on": [],
        }
    ]
    operations = [
        {
            "id": "op-a",
            "path": "/x",
            "query_keys": ["postMode"],
            "body_keys": ["product[0]", "selected[397]", "clientSecret"],
            "observations": [{"status": 302}],
            "count": 1,
        },
        {
            "id": "op-b",
            "path": "/x",
            "query_keys": ["postMode", "replace"],
            "body_keys": ["product", "vendors"],
            "observations": [{"status": 302}],
            "count": 1,
        },
    ]
    actions = [
        {
            "id": "bm.action.x",
            "method": "POST",
            "path": "/x",
            "statuses": {"302": 1},
            "form_fields": [
                {"name": "product[0]", "seen_in": 1},
                {"name": "clientSecret", "seen_in": 1},
            ],
            "query_key_sets": [["postMode"]],
            "confidence": "observed",
        }
    ]

    if reverse:
        endpoint_records.reverse()
        application_events.reverse()
        operations.reverse()

    endpoints_dir = root / "knowledge/http/endpoints"
    if split_endpoint_parts:
        endpoint_parts = [endpoint_records[:1], endpoint_records[1:]]
    else:
        endpoint_parts = [endpoint_records]
    endpoint_descriptors = []
    offset = 0
    for index, records in enumerate(endpoint_parts):
        name = f"part-{index:03d}.json"
        _dump(
            endpoints_dir / name,
            {"schema_version": "1.0", "records": records},
            pretty=reverse,
        )
        endpoint_descriptors.append(
            {"file": name, "records": len(records), "offset": offset}
        )
        offset += len(records)
    _dump(
        endpoints_dir / "index.json",
        {
            "schema_version": "1.0",
            "record_key": "records",
            "total_records": len(endpoint_records),
            "parts": endpoint_descriptors,
        },
        pretty=not reverse,
    )

    app_dir = root / "knowledge/http/application-events"
    app_dir.mkdir(parents=True, exist_ok=True)
    with (app_dir / "part-000.jsonl").open("w", encoding="utf-8") as handle:
        for event in application_events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=reverse) + "\n")
    _dump(
        app_dir / "index.json",
        {
            "schema_version": "1.0",
            "total_records": len(application_events),
            "parts": [
                {"file": "part-000.jsonl", "records": len(application_events), "offset": 0}
            ],
        },
    )

    forms_dir = root / "knowledge/http/forms"
    _dump(forms_dir / "part-000.json", forms, pretty=reverse)
    _dump(
        forms_dir / "index.json",
        {
            "schema_version": "1.0",
            "total_records": len(forms),
            "list_key": "forms",
            "parts": [{"file": "part-000.json", "records": len(forms), "offset": 0}],
        },
    )

    _dump(
        root / "knowledge/http/operation-index.json",
        {"schema_version": "1.0", "operations": operations},
        pretty=reverse,
    )
    _dump(
        root / "knowledge/actions/catalog.json",
        {"count": len(actions), "items": actions},
        pretty=not reverse,
    )


class RuntimeContractCompilationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RedactionPolicy.default()

    def test_application_events_preserve_method_status_and_query_relationships(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            compilation = BaselineCompiler.compile(root, self.policy)

            family = next(
                item for item in compilation.contract.endpoints if item.path_pattern == "/x"
            )
            get_contract = next(item for item in family.methods if item.method == "GET")
            post_contract = next(item for item in family.methods if item.method == "POST")

            self.assertEqual(get_contract.statuses, (200,))
            self.assertEqual(get_contract.query_key_sets, (("getMode",),))
            self.assertEqual(post_contract.statuses, (302,))
            self.assertEqual(post_contract.query_key_sets, (("postMode",),))

    def test_forms_keep_only_safe_structural_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            compilation = BaselineCompiler.compile(root, self.policy)

            signature = compilation.contract.forms[0]
            self.assertEqual(signature.method, "POST")
            self.assertEqual(signature.action_path, "/x")
            self.assertEqual(signature.field_names, ("product[n]", "safe"))
            self.assertFalse(hasattr(signature, "values"))
            self.assertNotIn("TOP_SECRET", repr(compilation.contract))

    def test_multiple_operation_signatures_are_preserved_and_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            compilation = BaselineCompiler.compile(root, self.policy)

            operations = [
                item for item in compilation.contract.operations if item.path_pattern == "/x"
            ]
            self.assertEqual(len(operations), 2)
            body_key_sets = {item.body_keys for item in operations}
            self.assertIn(("product[n]", "selected[n]"), body_key_sets)
            self.assertIn(("product", "vendors"), body_key_sets)

    def test_action_family_is_value_free_and_uses_endpoint_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            compilation = BaselineCompiler.compile(root, self.policy)

            action = compilation.contract.actions[0]
            self.assertEqual(action.action_id, "bm.action.x")
            self.assertEqual(action.method, "POST")
            self.assertEqual(action.path_pattern, "/x")
            self.assertEqual(action.field_names, ("product[n]",))
            self.assertEqual(action.query_key_sets, (("postMode",),))
            self.assertEqual(action.statuses, (302,))

    def test_semantically_equivalent_reordering_and_repartitioning_hash_identically(self):
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp)
            second = Path(second_tmp)
            _write_fixture(first, reverse=False, split_endpoint_parts=False)
            _write_fixture(second, reverse=True, split_endpoint_parts=True)

            first_result = BaselineCompiler.compile(first, self.policy)
            second_result = BaselineCompiler.compile(second, self.policy)

            self.assertEqual(first_result.contract, second_result.contract)
            self.assertEqual(first_result.baseline_sha256, second_result.baseline_sha256)
            self.assertRegex(first_result.baseline_sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(first_result.redaction_policy_sha256, r"^[0-9a-f]{64}$")


class BaselineFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RedactionPolicy.default()

    def test_partition_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            index_path = root / "knowledge/http/endpoints/index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["parts"][0]["file"] = "../escaped.json"
            _dump(index_path, index)
            _dump(root / "knowledge/http/escaped.json", {"records": []})

            with self.assertRaisesRegex(BaselineFormatError, "escapes dataset directory"):
                BaselineCompiler.compile(root, self.policy)

    def test_wrong_declared_partition_count_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            index_path = root / "knowledge/http/endpoints/index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["parts"][0]["records"] = 999
            _dump(index_path, index)

            with self.assertRaisesRegex(BaselineFormatError, "declares 999"):
                BaselineCompiler.compile(root, self.policy)

    def test_duplicate_partition_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            index_path = root / "knowledge/http/endpoints/index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["parts"].append(dict(index["parts"][0]))
            index["total_records"] *= 2
            _dump(index_path, index)

            with self.assertRaisesRegex(BaselineFormatError, "duplicate part"):
                BaselineCompiler.compile(root, self.policy)

    def test_unmatched_application_event_is_a_consistency_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            event_path = root / "knowledge/http/application-events/part-000.jsonl"
            lines = event_path.read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[0])
            event["path"] = "/not-in-endpoint-census"
            lines[0] = json.dumps(event)
            event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                BaselineConsistencyError,
                r"application-events/part-000\.jsonl:1.*not-in-endpoint-census",
            ):
                BaselineCompiler.compile(root, self.policy)

    def test_ambiguous_application_event_is_a_consistency_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            endpoint_path = root / "knowledge/http/endpoints/part-000.json"
            payload = json.loads(endpoint_path.read_text(encoding="utf-8"))
            payload["records"] = [
                {
                    "path_pattern": "/a/{id}",
                    "count": 1,
                    "methods": {"GET": 1},
                    "statuses": {"200": 1},
                    "query_keys": [],
                },
                {
                    "path_pattern": "/{kind}/b",
                    "count": 1,
                    "methods": {"GET": 1},
                    "statuses": {"200": 1},
                    "query_keys": [],
                },
            ]
            _dump(endpoint_path, payload)
            endpoint_index = root / "knowledge/http/endpoints/index.json"
            index = json.loads(endpoint_index.read_text(encoding="utf-8"))
            index["total_records"] = 2
            index["parts"][0]["records"] = 2
            _dump(endpoint_index, index)

            app_path = root / "knowledge/http/application-events/part-000.jsonl"
            event = {
                "capture": "fixture.har",
                "entry": 1,
                "method": "GET",
                "path": "/a/b",
                "query": {},
                "status": 200,
            }
            app_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            app_index = root / "knowledge/http/application-events/index.json"
            app_manifest = json.loads(app_index.read_text(encoding="utf-8"))
            app_manifest["total_records"] = 1
            app_manifest["parts"][0]["records"] = 1
            _dump(app_index, app_manifest)

            with self.assertRaisesRegex(BaselineConsistencyError, "ambiguous.*?/a/b"):
                BaselineCompiler.compile(root, self.policy)


class RepositoryRuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = BaselineCompiler.compile(
            REPO_ROOT,
            RedactionPolicy.default(),
        )

    def test_real_corpus_compiles_to_versioned_contract(self):
        contract = self.compilation.contract
        self.assertEqual(contract.contract_schema_version, 1)
        self.assertEqual(contract.normalization_version, 1)
        self.assertEqual(len(contract.endpoints), 68)
        self.assertGreater(len(contract.forms), 0)
        self.assertGreater(len(contract.operations), 0)
        self.assertEqual(len(contract.actions), 8)

    def test_numeric_message_query_keys_compile_to_one_structural_class(self):
        family = next(
            item
            for item in self.compilation.contract.endpoints
            if item.path_pattern == "/user/check/message"
        )
        get_contract = next(item for item in family.methods if item.method == "GET")
        self.assertEqual(get_contract.statuses, (302,))
        self.assertEqual(get_contract.query_key_sets, (("{numeric-key}",),))

    def test_vendor_select_keeps_distinct_operation_signatures(self):
        operations = [
            item
            for item in self.compilation.contract.operations
            if item.path_pattern == "/units/vendor/select/"
        ]
        self.assertGreaterEqual(len(operations), 3)
        body_keys = {key for operation in operations for key in operation.body_keys}
        self.assertIn("product[n]", body_keys)
        self.assertIn("selected[n]", body_keys)


if __name__ == "__main__":
    unittest.main()
