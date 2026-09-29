from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bizman.collector.network import (
    FirstPartyPolicy,
    NetworkNormalizer,
    ResponseBodyCaptureError,
)
from bizman.collector.storage import ArtifactStore
from bizman.foundation.redaction import RedactionPolicy


SESSION_ID = "01991c7d-a400-7000-8000-000000000401"


class ResponseBodyEvidenceTests(unittest.TestCase):
    def _normalizer(
        self,
        root: Path,
        *,
        max_response_body_bytes: int = 4 * 1024 * 1024,
    ) -> NetworkNormalizer:
        return NetworkNormalizer(
            session_id=SESSION_ID,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            redaction=RedactionPolicy.default(),
            artifacts=ArtifactStore(root),
            max_response_body_bytes=max_response_body_bytes,
        )

    def _prime_company_roster(self, normalizer: NetworkNormalizer) -> None:
        request = normalizer.normalize(
            method="Network.requestWillBeSent",
            params={
                "requestId": "r1",
                "timestamp": 1.0,
                "request": {
                    "url": (
                        "https://bizmania.ru/company/"
                        "?id=13393&tab=units&p=2"
                    ),
                    "method": "GET",
                    "headers": {},
                },
            },
            target_id="t1",
        )
        self.assertIsNotNone(request)
        response = normalizer.normalize(
            method="Network.responseReceived",
            params={
                "requestId": "r1",
                "timestamp": 1.1,
                "response": {
                    "url": (
                        "https://bizmania.ru/company/"
                        "?id=13393&tab=units&p=2"
                    ),
                    "status": 200,
                    "mimeType": "text/html",
                    "headers": {"Content-Type": "text/html; charset=utf-8"},
                },
            },
            target_id="t1",
        )
        self.assertIsNotNone(response)

    def test_only_authoritative_company_roster_is_capture_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime_company_roster(normalizer)
            self.assertTrue(normalizer.response_body_capture_candidate("r1"))

            normalizer.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "r2",
                    "timestamp": 2.0,
                    "request": {
                        "url": "https://bizmania.ru/units/shop/?id=33670",
                        "method": "GET",
                        "headers": {},
                    },
                },
                target_id="t1",
            )
            normalizer.normalize(
                method="Network.responseReceived",
                params={
                    "requestId": "r2",
                    "timestamp": 2.1,
                    "response": {
                        "url": "https://bizmania.ru/units/shop/?id=33670",
                        "status": 200,
                        "mimeType": "text/html",
                        "headers": {},
                    },
                },
                target_id="t1",
            )
            self.assertFalse(normalizer.response_body_capture_candidate("r2"))

    def test_sanitized_artifact_keeps_visible_roster_but_drops_script_and_attributes(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime_company_roster(normalizer)
            html = """<!doctype html>
<html>
<head><title>Компания Paradise · Предприятия</title></head>
<body>
<h1>Компания Paradise</h1>
<div>Предприятия</div>
<table>
<tr><th>Город</th><th>Предприятие</th><th>Уровень</th></tr>
<tr><td>Анкара</td><td>Детский магазин #33670</td><td>1</td></tr>
</table>
<input name="clientSecret" value="TOP_SECRET_INPUT">
<script>const token = "TOP_SECRET_SCRIPT";</script>
</body>
</html>"""
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=html,
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            assert event is not None
            self.assertEqual(event["event_type"], "http.response_body")
            self.assertEqual(event["url_path"], "/company/")
            self.assertEqual(event["query"]["id"], ["13393"])
            ref = event["response_body_ref"]
            self.assertIsInstance(ref, str)
            artifact = json.loads(normalizer.artifacts.read_bytes(ref))
            self.assertEqual(artifact["sanitizer_version"], 1)
            self.assertEqual(
                artifact["title"],
                "Компания Paradise · Предприятия",
            )
            self.assertIn("Детский магазин #33670", artifact["text"])
            serialized = json.dumps(artifact, ensure_ascii=False)
            self.assertNotIn("TOP_SECRET_INPUT", serialized)
            self.assertNotIn("TOP_SECRET_SCRIPT", serialized)
            self.assertNotIn("clientSecret", serialized)

    def test_response_body_size_limit_fails_closed_without_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(
                Path(tmp),
                max_response_body_bytes=32,
            )
            self._prime_company_roster(normalizer)
            before = normalizer.artifacts.count
            with self.assertRaises(ResponseBodyCaptureError):
                normalizer.normalize_response_body(
                    request_id="r1",
                    body="<html>" + ("x" * 128) + "</html>",
                    base64_encoded=False,
                    params={"requestId": "r1", "timestamp": 1.2},
                    target_id="t1",
                )
            self.assertEqual(normalizer.artifacts.count, before)


if __name__ == "__main__":
    unittest.main()
