import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bizman.ingest import (
    DATASET_LAYOUTS,
    HarFormatError,
    build_application_events,
    build_pages,
    is_first_party,
    load_har,
    write_dataset,
)
from bizman.ingest.__main__ import main

CAPTURE_NAME = "synthetic.har"
HTML_PAGE = (
    "<html><head><title>Проверка &middot; Тест</title>"
    '<script>var hidden = "/not/a/route";</script></head><body>'
    '<div id="menu">Меню</div><div id="content"><h1>Купить</h1>'
    "<p>Цена: 1&nbsp;000 p.</p></div>"
    '<div id="popupDiv">Скрытое</div></body></html>'
)
JSON_BODY = '{"b":2,"a":1}'
BASE64_PAYLOAD = b"hello-bytes"
BASE64_TEXT = base64.b64encode(BASE64_PAYLOAD).decode("ascii")


def _entry(
    url,
    *,
    method="GET",
    status=200,
    mime="text/html",
    resource_type="document",
    text=None,
    encoding=None,
    started="2026-09-06T11:32:00.000Z",
    size=None,
):
    content = {"mimeType": mime}
    if size is not None:
        content["size"] = size
    if encoding is not None:
        content["encoding"] = encoding
    if text is not None:
        content["text"] = text
    return {
        "startedDateTime": started,
        "request": {"method": method, "url": url},
        "response": {"status": status, "content": content},
        "_resourceType": resource_type,
    }


def _synthetic_document():
    page_size = len(HTML_PAGE.encode("utf-8"))
    json_size = len(JSON_BODY.encode("utf-8"))
    return {
        "log": {
            "version": "1.2",
            "entries": [
                _entry(
                    "https://bizmania.ru/units/shop/?id=42&id=43&tab=",
                    started="2026-09-06T14:31:55.887+03:00",
                    size=page_size,
                    text=HTML_PAGE,
                ),
                _entry(
                    "https://bizmania.ru/fl/sprites/test.json/?x=1",
                    mime="application/json",
                    resource_type="xhr",
                    text=JSON_BODY,
                    size=json_size,
                ),
                _entry(
                    "https://bizmania.ru/user/splash/id/abcde",
                    mime="image/png",
                    resource_type="image",
                    encoding="base64",
                    text=BASE64_TEXT,
                    size=len(BASE64_PAYLOAD),
                ),
                _entry(
                    "https://mc.yandex.ru/watch/1",
                    text="<html>tracker</html>",
                    size=len(b"<html>tracker</html>"),
                ),
            ],
        }
    }


class IngestHarTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.har_path = self.tmp / CAPTURE_NAME
        self.document = _synthetic_document()
        self.write_document(self.document)

    def write_document(self, document):
        self.har_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

    def load(self):
        return load_har(self.har_path)

    def captures(self):
        return {CAPTURE_NAME: self.load()}


class ReaderTests(IngestHarTestCase):
    def test_entry_fields_are_normalized(self):
        entries = self.load()
        self.assertEqual(len(entries), 4)
        page = entries[0]
        self.assertEqual(page.index, 0)
        self.assertEqual(page.started_at, "2026-09-06T11:31:55.887Z")
        self.assertEqual(page.method, "GET")
        self.assertEqual(page.path, "/units/shop/")
        self.assertEqual(page.query, {"id": "42", "tab": ""})
        self.assertEqual(page.status, 200)
        self.assertEqual(page.mime_type, "text/html")
        self.assertEqual(page.resource_type, "document")
        self.assertEqual(page.body_text, HTML_PAGE)
        self.assertEqual(page.response_size, len(HTML_PAGE.encode("utf-8")))
        expected_sha = hashlib.sha256(HTML_PAGE.encode("utf-8")).hexdigest()
        self.assertEqual(page.response_text_sha256, expected_sha)

    def test_base64_body_is_decoded_and_hashed_over_text(self):
        splash = self.load()[2]
        self.assertEqual(splash.body_text, "hello-bytes")
        self.assertEqual(splash.response_size, len(BASE64_PAYLOAD))
        expected_sha = hashlib.sha256(BASE64_TEXT.encode("utf-8")).hexdigest()
        self.assertEqual(splash.response_text_sha256, expected_sha)

    def test_missing_body_has_no_digest(self):
        entry = _entry("https://bizmania.ru/user/check/message", status=302)
        entry["response"]["content"]["size"] = 0
        document = {"log": {"entries": [entry]}}
        self.write_document(document)
        loaded = self.load()[0]
        self.assertEqual(loaded.body_text, "")
        self.assertEqual(loaded.response_size, 0)
        self.assertIsNone(loaded.response_text_sha256)

    def test_first_party_filter(self):
        entries = self.load()
        self.assertTrue(is_first_party(entries[0]))
        self.assertFalse(is_first_party(entries[3]))


class FailClosedTests(IngestHarTestCase):
    def test_malformed_json(self):
        self.har_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(HarFormatError):
            self.load()

    def test_missing_entries(self):
        self.write_document({"log": {"version": "1.2"}})
        with self.assertRaises(HarFormatError):
            self.load()

    def test_bad_entry_type(self):
        self.write_document({"log": {"entries": [42]}})
        with self.assertRaises(HarFormatError):
            self.load()

    def test_invalid_base64_padding(self):
        self.write_document(
            {
                "log": {
                    "entries": [
                        _entry(
                            "https://bizmania.ru/x",
                            encoding="base64",
                            text="abc",
                        )
                    ]
                }
            }
        )
        with self.assertRaises(HarFormatError):
            self.load()

    def test_oversized_file_fails_closed(self):
        with mock.patch("bizman.ingest.har.MAX_HAR_BYTES", 10):
            with self.assertRaises(HarFormatError):
                self.load()

    def test_oversized_body_fails_closed(self):
        with mock.patch("bizman.ingest.har.MAX_BODY_BYTES", 4):
            with self.assertRaises(HarFormatError):
                self.load()


class GeneratorTests(IngestHarTestCase):
    def test_application_events_exact(self):
        expected = [
            {
                "capture": CAPTURE_NAME,
                "entry": 0,
                "started_at": "2026-09-06T11:31:55.887Z",
                "method": "GET",
                "path": "/units/shop/",
                "query": {"id": "42", "tab": ""},
                "status": 200,
                "mime_type": "text/html",
                "resource_type": "document",
                "response_size": len(HTML_PAGE.encode("utf-8")),
                "response_text_sha256": hashlib.sha256(
                    HTML_PAGE.encode("utf-8")
                ).hexdigest(),
            },
            {
                "capture": CAPTURE_NAME,
                "entry": 1,
                "started_at": "2026-09-06T11:32:00.000Z",
                "method": "GET",
                "path": "/fl/sprites/test.json/",
                "query": {"x": "1"},
                "status": 200,
                "mime_type": "application/json",
                "resource_type": "xhr",
                "response_size": len(JSON_BODY.encode("utf-8")),
                "response_text_sha256": hashlib.sha256(
                    JSON_BODY.encode("utf-8")
                ).hexdigest(),
            },
            {
                "capture": CAPTURE_NAME,
                "entry": 2,
                "started_at": "2026-09-06T11:32:00.000Z",
                "method": "GET",
                "path": "/user/splash/id/abcde",
                "query": {},
                "status": 200,
                "mime_type": "image/png",
                "resource_type": "image",
                "response_size": len(BASE64_PAYLOAD),
                "response_text_sha256": hashlib.sha256(
                    BASE64_TEXT.encode("utf-8")
                ).hexdigest(),
            },
        ]
        self.assertEqual(build_application_events(self.captures()), expected)

    def test_pages_exact(self):
        expected = [
            {
                "capture": CAPTURE_NAME,
                "entry": 0,
                "started_at": "2026-09-06T11:31:55.887Z",
                "method": "GET",
                "path": "/units/shop/",
                "query": {"id": "42", "tab": ""},
                "status": 200,
                "title": "Проверка · Тест",
                "text": "Купить\nЦена: 1\xa0000 p.",
                "html_sha256": hashlib.sha256(
                    HTML_PAGE.encode("utf-8")
                ).hexdigest(),
            }
        ]
        self.assertEqual(build_pages(self.captures()), expected)


class WriterTests(IngestHarTestCase):
    def test_write_dataset_jsonl_and_read_back(self):
        records = build_application_events(self.captures())
        manifest_path = write_dataset(
            self.tmp / "out", "application-events", records, jsonl=True
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["total_records"], 3)
        self.assertEqual(len(manifest["parts"]), 1)
        part = Path(manifest_path).parent / manifest["parts"][0]["file"]
        lines = [line for line in part.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual([json.loads(line) for line in lines], records)

    def test_write_dataset_wrapper(self):
        records = [{"path": "/x", "count": 1}]
        manifest_path = write_dataset(
            self.tmp / "out",
            "endpoints",
            records,
            jsonl=False,
            wrapper_key="records",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        part = Path(manifest_path).parent / manifest["parts"][0]["file"]
        payload = json.loads(part.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["records"], records)


class CliCompareTests(IngestHarTestCase):
    def _run(self, knowledge_dir):
        out = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(out), redirect_stderr(errors):
            code = main(
                [
                    "--har",
                    str(self.har_path),
                    "--out",
                    str(self.tmp / "out"),
                    "--dataset",
                    "application-events",
                    "--compare",
                    str(knowledge_dir),
                ]
            )
        return code, out.getvalue()

    def test_compare_match_exits_zero(self):
        records = build_application_events(self.captures())
        write_dataset(self.tmp / "knowledge" / "http", "application-events", records, jsonl=True)
        code, output = self._run(self.tmp / "knowledge")
        self.assertEqual(code, 0)
        self.assertIn("match", output)

    def test_compare_mismatch_exits_one(self):
        records = build_application_events(self.captures())
        records[0] = dict(records[0], status=500)
        write_dataset(self.tmp / "knowledge" / "http", "application-events", records, jsonl=True)
        code, output = self._run(self.tmp / "knowledge")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", output)
        self.assertIn("committed-only", output)
        self.assertIn("generated-only", output)


class LayoutTests(unittest.TestCase):
    def test_all_acceptance_datasets_are_registered(self):
        self.assertEqual(
            set(DATASET_LAYOUTS),
            {
                "application-events",
                "endpoints",
                "routes",
                "forms",
                "json-responses",
                "game-html-pages",
                "assets",
                "wiki-topics",
            },
        )


if __name__ == "__main__":
    unittest.main()
