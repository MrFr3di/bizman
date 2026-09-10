from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]


def _capture(callable_, *args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = callable_(*args)
    return code, stdout.getvalue(), stderr.getvalue()


class UnifiedCliParityTests(unittest.TestCase):
    def test_detect_json_output_matches_legacy_default_policy_path(self):
        from bizman.cli.main import main as new_main
        from tools.detect_changes import main as legacy_main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            legacy = _capture(
                legacy_main,
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--dry-run",
                ],
            )
            modern = _capture(
                new_main,
                [
                    "detect",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--dry-run",
                ],
            )

            self.assertEqual(modern, legacy)
            self.assertEqual(modern[0], 0)
            self.assertEqual(modern[2], "")
            self.assertEqual(len(modern[1].splitlines()), 1)
            self.assertFalse((data_dir / "detector").exists())
            self.assertFalse((data_dir / "promotions").exists())

    def test_validate_output_and_exit_semantics_match_legacy(self):
        from bizman.cli.main import main as new_main
        from tools.validate_repo import main as legacy_main

        legacy = _capture(legacy_main)
        modern = _capture(
            new_main,
            ["validate", "--repo-root", str(REPO_ROOT)],
        )
        self.assertEqual(modern, legacy)
        self.assertEqual(modern[0], 0)
        self.assertIn("VALIDATION OK", modern[1])

    def test_collect_ctrl_c_returns_130(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            fake_collect = AsyncMock(side_effect=KeyboardInterrupt())
            with patch("bizman.cli.collect.collect", fake_collect):
                code, stdout, stderr = _capture(
                    main,
                    [
                        "collect",
                        "--repo-root",
                        str(REPO_ROOT),
                        "--data-dir",
                        str(Path(tmp) / "BizManData"),
                    ],
                )

        self.assertEqual(code, 130)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Collection interrupted.\n")

    def test_bizman_error_has_stable_cli_exit_and_message(self):
        from bizman.cli.main import main
        from bizman.core import OperationError

        with tempfile.TemporaryDirectory() as tmp:
            fake_collect = AsyncMock(side_effect=OperationError("collector unavailable"))
            with patch("bizman.cli.collect.collect", fake_collect):
                code, stdout, stderr = _capture(
                    main,
                    [
                        "collect",
                        "--repo-root",
                        str(REPO_ROOT),
                        "--data-dir",
                        str(Path(tmp) / "BizManData"),
                    ],
                )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "ERROR: collector unavailable\n")

    def test_legacy_scripts_are_thin_cli_delegates(self):
        expected = {
            "tools/collect_live.py": "bizman.cli.collect",
            "tools/detect_changes.py": "bizman.cli.detect",
            "tools/validate_repo.py": "bizman.cli.validate",
        }
        for relative, import_name in expected.items():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(import_name, text, relative)
            self.assertNotIn("tools.bizman_collector", text, relative)
            self.assertNotIn("tools.bizman_detector", text, relative)
            self.assertNotIn("bizman_foundation", text, relative)


if __name__ == "__main__":
    unittest.main()
