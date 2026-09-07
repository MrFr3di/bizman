import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_foundation.validation import validate_repository


class BizManDataGuardTests(unittest.TestCase):
    def test_validator_rejects_accidental_bizman_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge/sources").mkdir(parents=True)
            (root / "schemas").mkdir()
            (root / "knowledge/catalog.json").write_text(
                json.dumps({"schema_version": "2.0", "datasets": []}),
                encoding="utf-8",
            )
            event_dir = root / "BizManData" / "events"
            event_dir.mkdir(parents=True)
            (event_dir / "session.jsonl").write_text('{"event":1}\n', encoding="utf-8")

            result = validate_repository(root)

            self.assertTrue(
                any("forbidden committed file" in error for error in result.errors),
                result.errors,
            )


if __name__ == "__main__":
    unittest.main()
