from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from bizman.cli.main import main


class ProductProbeCliTests(unittest.TestCase):
    def test_probe_prints_only_structural_candidate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "goods.html"
            body.write_text(
                '<a href="/products/?id=42">TOP_SECRET_LABEL</a>'
                '<input type="hidden" value="TOP_SECRET_VALUE">',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "probe-product-evidence",
                        "--repo-root",
                        str(Path.cwd()),
                        "--source-url",
                        "https://bizmania.ru/units/shop/?id=33670&tab=goods",
                        "--body-file",
                        str(body),
                    ]
                )
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"unit_id":33670', output)
        self.assertIn('"numeric_query_ids":[42]', output)
        self.assertNotIn("TOP_SECRET_LABEL", output)
        self.assertNotIn("TOP_SECRET_VALUE", output)
        self.assertEqual(stderr.getvalue(), "")

    def test_probe_rejects_unsupported_route_without_echoing_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "goods.html"
            body.write_text("<p>irrelevant</p>", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "probe-product-evidence",
                        "--repo-root",
                        str(Path.cwd()),
                        "--source-url",
                        "https://bizmania.ru/units/shop/?id=33670&tab=supply&token=TOP_SECRET",
                        "--body-file",
                        str(body),
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertNotIn("TOP_SECRET", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
