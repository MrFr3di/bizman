from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from bizman.cli.main import main


class SupplyProbeCliTests(unittest.TestCase):
    @staticmethod
    def sample_html() -> str:
        return """
        <form action="/units/shop/" method="post">
          <table>
            <tr>
              <td>
                <a href="/units/vendor/?id=777&product=418&token=TOP_SECRET">
                  TOP_SECRET_LABEL
                </a>
              </td>
              <td>
                <input type="hidden" name="vendor[0]" value="TOP_SECRET_VENDOR">
                <input name="vendorQuantity[0]" value="TOP_SECRET_QUANTITY">
                <input name="vendorPrice[0]" value="TOP_SECRET_PRICE">
              </td>
            </tr>
          </table>
        </form>
        """

    def run_body(
        self,
        html: str,
        *,
        source_url: str = "https://bizmania.ru/units/shop/?id=33676&tab=supply",
    ) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "supply.html"
            body.write_text(html, encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "probe-supply-evidence",
                        "--repo-root",
                        str(Path.cwd()),
                        "--source-url",
                        source_url,
                        "--body-file",
                        str(body),
                    ]
                )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_probe_prints_structural_metadata_only(self) -> None:
        code, output, error = self.run_body(self.sample_html())
        self.assertEqual((code, error), (0, ""))

        report = json.loads(output)
        self.assertEqual(report["schema"], "bizman.supply-probe.v1")
        self.assertEqual(report["unit_id"], 33676)
        self.assertEqual(report["surface"], "shop.supply")
        self.assertEqual(report["link_count"], 1)
        self.assertEqual(report["input_count"], 3)
        self.assertEqual(
            report["links"][0],
            {
                "path": "/units/vendor/",
                "query_keys": ["id", "product", "token"],
                "numeric_query_fields": [["id", 777], ["product", 418]],
                "in_row": True,
                "in_form": True,
            },
        )
        self.assertEqual(
            [(item["name"], item["index"]) for item in report["inputs"]],
            [
                ("vendor", 0),
                ("vendorQuantity", 0),
                ("vendorPrice", 0),
            ],
        )
        for forbidden in (
            "TOP_SECRET",
            "TOP_SECRET_LABEL",
            "TOP_SECRET_VENDOR",
            "TOP_SECRET_QUANTITY",
            "TOP_SECRET_PRICE",
        ):
            self.assertNotIn(forbidden, output)

    def test_rejects_goods_extra_query_and_secret_url_without_echoing_it(self) -> None:
        rejected = (
            "https://bizmania.ru/units/shop/?id=33676&tab=goods",
            "https://bizmania.ru/units/shop/?id=33676&tab=supply&product=418",
            "https://bizmania.ru/units/shop/?id=33676&tab=supply&token=TOP_SECRET",
        )
        for url in rejected:
            with self.subTest(url=url):
                code, output, error = self.run_body(
                    "<p>TOP_SECRET_BODY</p>",
                    source_url=url,
                )
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertNotIn("TOP_SECRET", error)
                self.assertIn("SupplyProbeRouteError", error)

    def test_ambiguous_structure_is_nonzero_without_echoing_values(self) -> None:
        code, output, error = self.run_body(
            '<input name="vendor[0]" name="vendor[1]" '
            'value="TOP_SECRET_VALUE">'
        )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("SupplyProbeStructureError", error)
        self.assertNotIn("TOP_SECRET", error)

    def test_oversized_input_is_rejected_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "supply.html"
            body.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "probe-supply-evidence",
                        "--repo-root",
                        str(Path.cwd()),
                        "--source-url",
                        "https://bizmania.ru/units/shop/?id=33676&tab=supply",
                        "--body-file",
                        str(body),
                    ]
                )
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "ERROR: supply evidence probe input exceeds size limit\n",
        )


if __name__ == "__main__":
    unittest.main()
