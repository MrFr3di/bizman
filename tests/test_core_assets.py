from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class RepositoryAssetsTests(unittest.TestCase):
    def _write_required_assets(self, root: Path) -> None:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "schemas").mkdir(parents=True, exist_ok=True)
        (root / "knowledge").mkdir(parents=True, exist_ok=True)
        (root / "config/redaction-policy.json").write_text("{}", encoding="utf-8")
        for name in (
            "event.schema.json",
            "session-manifest.schema.json",
            "promotion-bundle.schema.json",
        ):
            (root / "schemas" / name).write_text("{}", encoding="utf-8")

    def test_exact_asset_mapping_and_enum_only_lookup(self):
        from bizman.core.assets import AssetId, RepositoryAssets

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._write_required_assets(root)
            assets = RepositoryAssets(root)

            expected = {
                AssetId.REDACTION_POLICY: root / "config/redaction-policy.json",
                AssetId.EVENT_SCHEMA: root / "schemas/event.schema.json",
                AssetId.SESSION_MANIFEST_SCHEMA: root / "schemas/session-manifest.schema.json",
                AssetId.PROMOTION_SCHEMA: root / "schemas/promotion-bundle.schema.json",
                AssetId.KNOWLEDGE_ROOT: root / "knowledge",
            }
            self.assertEqual(set(AssetId), set(expected))
            for asset_id, path in expected.items():
                self.assertEqual(assets.path(asset_id), path.resolve())

            with self.assertRaises(TypeError):
                assets.path("event_schema")  # type: ignore[arg-type]

    def test_missing_required_asset_fails_closed(self):
        from bizman.core.assets import RepositoryAssets

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._write_required_assets(root)
            (root / "schemas/event.schema.json").unlink()
            with self.assertRaises(FileNotFoundError):
                RepositoryAssets(root)

    def test_symlink_escape_is_rejected(self):
        from bizman.core.assets import RepositoryAssets

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            self._write_required_assets(root)
            outside = base / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            event_schema = root / "schemas/event.schema.json"
            event_schema.unlink()
            try:
                event_schema.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            with self.assertRaises(ValueError):
                RepositoryAssets(root)


if __name__ == "__main__":
    unittest.main()
