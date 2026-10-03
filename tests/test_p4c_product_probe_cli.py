from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from bizman.cli.main import main


class ProductProbeCliTests(unittest.TestCase):
    # Sanitized shape observed in src.c0.20261003.a1, goods table rows 92-146.
    # Capture provenance remains local in .work/capture-manifest.json.
    @staticmethod
    def goods_row(product: int, *, unit: int = 33670) -> str:
        href = f'/units/shop/?id={unit}&amp;tab=goods&amp;product={product}'
        return f'<tr><td><a href="{href}"><img></a></td><td><a href="{href}">SECRET_LABEL</a></td></tr>'

    def run_body(self, html: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "goods.html"
            body.write_text(html, encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([
                    "probe-product-evidence", "--repo-root", str(Path.cwd()),
                    "--source-url", "https://bizmania.ru/units/shop/?id=33670&tab=goods",
                    "--body-file", str(body),
                ])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_goods_rows_deduplicate_agreeing_links_and_ignore_labels_and_order(self) -> None:
        for ids in ((121, 377), (377, 121)):
            html = '<table id="goods"><tbody>' + ''.join(self.goods_row(i) for i in ids) + '</tbody></table>'
            code, output, error = self.run_body(html)
            self.assertEqual((code, error), (0, ""))
            data = json.loads(output)
            self.assertEqual(data["candidate_count"], 2)
            self.assertEqual([c["numeric_query_ids"] for c in data["candidates"]], [[i] for i in ids])
            self.assertNotIn("SECRET", output)

    def test_goods_rejects_ambiguous_or_untrusted_rows_without_echoing_input(self) -> None:
        row = self.goods_row(121)
        invalid = [
            row.replace('product=121', 'product=377', 1),
            row + row,
            self.goods_row(121, unit=33676),
            row.replace('tab=goods', 'tab=supply'),
            row.replace('product=121', 'product=0121'),
            row.replace('product=121', 'product=0'),
            row.replace('product=121', 'product=9223372036854775808'),
            row.replace('product=121', 'product=%31'),
            row.replace('product=121', 'product=121&amp;token=SECRET'),
            row.replace('product=121', 'product=121&amp;product=121'),
            row.replace('href="', 'href="javascript:SECRET" href="', 1),
            row.replace('<tr>', '<tr class="x" class="y">'),
            row.replace('<td><a', '<td hidden><a', 1),
            row.replace('/units/shop/', 'https://bizmania.ru/units/shop/'),
            row.replace('product=121', 'product=121#SECRET'),
            row.replace('product=121', 'product=+121'),
            row.replace('product=121', 'product=１２１'),
        ]
        for html in invalid:
            with self.subTest(html=html):
                code, output, error = self.run_body('<table id="goods">' + html + '</table>')
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertNotIn("SECRET", error)

    def test_goods_missing_empty_duplicate_or_incomplete_table_is_unknown(self) -> None:
        table = '<table id="goods">' + self.goods_row(121) + '</table>'
        for html in ('<p>SECRET</p>', '<table id="goods"></table>', table + table,
                     table.replace('</table>', ''), table.replace('</tr>', '')):
            with self.subTest(html=html):
                code, output, error = self.run_body(html)
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertNotIn("SECRET", error)

    def test_goods_excludes_hidden_inactive_and_outside_rows(self) -> None:
        row = self.goods_row(377)
        hidden = '<template>' + row + '</template>' + row.replace('<tr>', '<tr hidden>')
        html = row + '<table id="goods"><tr><th>Heading</th></tr>' + hidden + self.goods_row(121) + '</table>'
        code, output, error = self.run_body(html)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["candidates"], [{"tag": "a", "attribute": "href", "numeric_query_ids": [121]}])

    def test_goods_overflow_fails_instead_of_truncating(self) -> None:
        code, output, error = self.run_body('<table id="goods">' + ''.join(self.goods_row(i + 1) for i in range(65)) + '</table>')
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("ProductProbeCandidateOverflowError", error)

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

    def test_probe_candidate_overflow_is_nonzero_without_leaking_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "goods.html"
            body.write_text(
                "".join(
                    f'<a href="/products/?id={index + 1}">TOP_SECRET_{index}</a>'
                    for index in range(65)
                ),
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

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ProductProbeCandidateOverflowError", stderr.getvalue())
        self.assertNotIn("TOP_SECRET", stderr.getvalue())

    def test_probe_rejects_oversized_input_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "goods.html"
            body.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
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

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "ERROR: product evidence probe input exceeds size limit\n",
        )


if __name__ == "__main__":
    unittest.main()
