from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.baseline import BaselineCompiler, BaselineConsistencyError
from tools.bizman_foundation.redaction import RedactionPolicy


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_dynamic_fixture(root: Path, *, action_query_keys: list[str] | None = None) -> None:
    endpoint = {
        "path_pattern": "/items/{item_id}",
        "count": 1,
        "methods": {"POST": 1},
        "statuses": {"200": 1},
        "query_keys": [],
    }
    endpoints_dir = root / "knowledge/http/endpoints"
    _dump(endpoints_dir / "part-000.json", {"records": [endpoint]})
    _dump(
        endpoints_dir / "index.json",
        {
            "record_key": "records",
            "total_records": 1,
            "parts": [{"file": "part-000.json", "records": 1, "offset": 0}],
        },
    )

    app_dir = root / "knowledge/http/application-events"
    app_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "method": "POST",
        "path": "/items/123",
        "query": {},
        "status": 200,
    }
    (app_dir / "part-000.jsonl").write_text(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _dump(
        app_dir / "index.json",
        {
            "total_records": 1,
            "parts": [{"file": "part-000.jsonl", "records": 1, "offset": 0}],
        },
    )

    forms_dir = root / "knowledge/http/forms"
    _dump(
        forms_dir / "part-000.json",
        [
            {
                "method": "POST",
                "action": "/items/123?view=ignored",
                "fields": [{"name": "safe", "value": "DO_NOT_KEEP"}],
            }
        ],
    )
    _dump(
        forms_dir / "index.json",
        {
            "total_records": 1,
            "list_key": "forms",
            "parts": [{"file": "part-000.json", "records": 1, "offset": 0}],
        },
    )

    _dump(
        root / "knowledge/http/operation-index.json",
        {
            "post_count": 1,
            "operations": [
                {
                    "path": "/items/123",
                    "query_keys": [],
                    "body_keys": ["safe"],
                    "observations": [{"status": 200}],
                    "count": 1,
                }
            ],
        },
    )
    _dump(
        root / "knowledge/actions/catalog.json",
        {
            "count": 1,
            "items": [
                {
                    "id": "bm.action.item.update",
                    "method": "POST",
                    "path": "/items/123",
                    "observed_count": 1,
                    "statuses": {"200": 1},
                    "form_fields": [{"name": "safe", "seen_in": 1}],
                    "query_key_sets": [action_query_keys or []],
                    "confidence": "observed",
                }
            ],
        },
    )


class BaselineHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RedactionPolicy.default()

    def test_form_action_is_canonicalized_to_unique_endpoint_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dynamic_fixture(root)

            compilation = BaselineCompiler.compile(root, self.policy)

            self.assertEqual(compilation.contract.forms[0].action_path, "/items/{item_id}")

    def test_action_query_shape_must_be_backed_by_operation_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dynamic_fixture(root, action_query_keys=["unexpected"])

            with self.assertRaisesRegex(BaselineConsistencyError, "action.*operation"):
                BaselineCompiler.compile(root, self.policy)


if __name__ == "__main__":
    unittest.main()
