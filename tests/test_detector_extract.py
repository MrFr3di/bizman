from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from tools.bizman_detector.evidence import EvidenceIntegrityError, EvidenceReader
from tools.bizman_detector.extract import ExtractionIntegrityError, ObservationExtractor
from tools.bizman_detector.model import (
    EndpointFamily,
    EndpointMethodContract,
    EndpointVariant,
    RuntimeContract,
)
from tools.bizman_foundation.redaction import RedactionPolicy


SESSION_ID = "01991c7d-a400-7000-8000-000000000001"


def _uuid7(index: int) -> str:
    return f"01991c7d-a400-7000-8000-{index:012x}"


def _base_event(sequence: int, *, source: str, event_type: str, **extra: object) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": _uuid7(sequence + 10),
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": source,
        "event_type": event_type,
        "confidence": "observed" if source != "system" else "inferred",
    }
    event.update(extra)
    return event


def _request(
    sequence: int,
    *,
    request_id: str,
    path: str,
    method: str = "POST",
    query: dict[str, list[str]] | None = None,
    body_ref: str | None = None,
    content_type: str | None = None,
) -> dict[str, object]:
    headers: dict[str, str] = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return _base_event(
        sequence,
        source="cdp.network",
        event_type="http.request",
        request_id=request_id,
        redirect_index=0,
        method=method,
        url_path=path,
        route_pattern=None,
        status_code=None,
        query=query or {},
        headers=headers,
        request_body_ref=body_ref,
        response_body_ref=None,
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
    )


def _response(sequence: int, *, request_id: str, path: str, status: int = 200) -> dict[str, object]:
    return _base_event(
        sequence,
        source="cdp.network",
        event_type="http.response",
        request_id=request_id,
        redirect_index=0,
        method="POST",
        url_path=path,
        route_pattern=None,
        status_code=status,
        query={},
        headers={},
        request_body_ref=None,
        response_body_ref=None,
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
    )


def _action(sequence: int, *, path: str = "/units/42/save") -> dict[str, object]:
    return _base_event(
        sequence,
        source="dom.action",
        event_type="dom.action",
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
        action_kind="submit",
        is_trusted=True,
        page_path="/units/42",
        element_tag="form",
        element_type=None,
        element_name=None,
        element_role=None,
        safe_selector="form#save",
        form_action_path=path,
        form_method="POST",
        form_field_names=["product[17]", "safe"],
    )


def _correlation(
    sequence: int,
    *,
    action_event_id: str,
    request_event_id: str,
    status: str = "strong",
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="system",
        event_type="correlation.action_http",
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[action_event_id],
        action_event_id=action_event_id,
        network_event_id=request_event_id,
        correlation_status=status,
        correlation_score=0.95,
        delta_ms=20.0,
        signals=["form-path-match", "method-match"],
    )


class _SessionFixture:
    def __init__(self, root: Path):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.data_dir = root / "BizManData"
        self.event_rel = f"events/2026-09-07/{SESSION_ID}.jsonl"
        self.event_path = self.data_dir / self.event_rel
        self.manifest_path = self.data_dir / "sessions" / SESSION_ID / "manifest.json"

    def put_artifact(self, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.data_dir / "artifacts" / "sha256" / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return f"sha256:{digest}"

    def write(self, events: list[dict[str, object]]) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self.event_path.write_bytes(
            b"".join(
                (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for event in events
            )
        )
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0",
            "session_id": SESSION_ID,
            "started_at": "2026-09-07T12:00:00Z",
            "ended_at": "2026-09-07T12:01:00Z",
            "status": "completed",
            "collector": {"name": "bizman-cdp", "version": "0.2.1"},
            "browser": {"product": "Chrome", "version": "140.0"},
            "protocol": {"name": "cdp", "version": "1.3", "sha256": None, "artifact_ref": None},
            "event_files": [self.event_rel],
            "artifact_count": 0,
            "warnings": [],
        }
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def reader(self) -> EvidenceReader:
        return EvidenceReader(self.repo_root, self.data_dir)


def _contract() -> RuntimeContract:
    return RuntimeContract(
        contract_schema_version=1,
        normalization_version=1,
        endpoints=(
            EndpointFamily(
                "/units/{id}/save",
                (
                    EndpointMethodContract(
                        "POST",
                        (EndpointVariant(query_keys=("id",), status=200),),
                    ),
                ),
            ),
        ),
        forms=(),
        operations=(),
        actions=(),
    )


class ObservationExtractorTests(unittest.TestCase):
    def _extract(self, fixture: _SessionFixture, **kwargs: object):
        reader = fixture.reader()
        identity = reader.inspect(SESSION_ID)
        extractor = ObservationExtractor(
            _contract(),
            reader,
            RedactionPolicy.default(),
            **kwargs,
        )
        return extractor.extract(identity)

    def test_missing_body_ref_remains_unknown_and_projection_is_value_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            fixture.write(
                [
                    _request(
                        0,
                        request_id="r1",
                        path="/units/42/save",
                        query={"id": ["SYNTHETIC-QUERY-VALUE"]},
                    ),
                    _response(1, request_id="r1", path="/units/42/save"),
                ]
            )
            observations = self._extract(fixture)
            self.assertEqual(len(observations.http), 1)
            http = observations.http[0]
            self.assertIsNone(http.body_keys)
            self.assertEqual(http.query_keys, ("id",))
            self.assertEqual(http.canonical_path_pattern, "/units/{id}/save")
            self.assertEqual(http.status, 200)
            self.assertNotIn("SYNTHETIC-QUERY-VALUE", repr(observations))

    def test_verified_json_and_form_bodies_extract_only_normalized_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            json_ref = fixture.put_artifact(
                json.dumps(
                    [
                        {"product[17]": "SYNTHETIC-BODY-VALUE", "safe": "one"},
                        {"product[23]": "other", "extra": "two"},
                    ],
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            form_ref = fixture.put_artifact(
                urlencode({"selected[397][2]": "SYNTHETIC-FORM-VALUE", "safe": "yes"}).encode("utf-8")
            )
            fixture.write(
                [
                    _request(
                        0,
                        request_id="r1",
                        path="/units/42/save",
                        body_ref=json_ref,
                        content_type="application/json; charset=utf-8",
                    ),
                    _request(
                        1,
                        request_id="r2",
                        path="/units/43/save",
                        body_ref=form_ref,
                        content_type="application/x-www-form-urlencoded",
                    ),
                ]
            )
            observations = self._extract(fixture)
            self.assertEqual(
                [item.body_keys for item in observations.http],
                [("extra", "product[n]", "safe"), ("safe", "selected[n][n]")],
            )
            rendered = repr(observations)
            self.assertNotIn("SYNTHETIC-BODY-VALUE", rendered)
            self.assertNotIn("SYNTHETIC-FORM-VALUE", rendered)

    def test_referenced_malformed_or_unsupported_body_is_integrity_error(self):
        cases = (
            (b"{not-json", "application/json"),
            (b"safe=value", "text/plain"),
        )
        for payload, content_type in cases:
            with self.subTest(content_type=content_type), tempfile.TemporaryDirectory() as tmp:
                fixture = _SessionFixture(Path(tmp))
                body_ref = fixture.put_artifact(payload)
                fixture.write(
                    [
                        _request(
                            0,
                            request_id="r1",
                            path="/units/42/save",
                            body_ref=body_ref,
                            content_type=content_type,
                        )
                    ]
                )
                with self.assertRaises(ExtractionIntegrityError):
                    self._extract(fixture)

    def test_forms_and_relations_resolve_to_value_free_structural_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            action = _action(0)
            request = _request(1, request_id="r1", path="/units/42/save")
            link = _correlation(
                2,
                action_event_id=str(action["event_id"]),
                request_event_id=str(request["event_id"]),
            )
            fixture.write([action, request, link])
            observations = self._extract(fixture)
            self.assertEqual(len(observations.forms), 1)
            self.assertEqual(observations.forms[0].action_path, "/units/{id}/save")
            self.assertEqual(observations.forms[0].field_names, ("product[n]", "safe"))
            self.assertEqual(len(observations.relations), 1)
            relation = observations.relations[0]
            self.assertEqual(relation.correlation_status, "strong")
            self.assertEqual(relation.action_path, "/units/{id}/save")
            self.assertEqual(relation.request_path_pattern, "/units/{id}/save")
            self.assertEqual(relation.request_method, "POST")

    def test_relation_sources_are_same_session_resolved_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            fixture.write(
                [
                    _correlation(
                        0,
                        action_event_id=_uuid7(900),
                        request_event_id=_uuid7(901),
                    )
                ]
            )
            with self.assertRaises(ExtractionIntegrityError):
                self._extract(fixture)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            fixture.write(
                [
                    _action(0),
                    _request(1, request_id="r1", path="/units/42/save"),
                ]
            )
            with self.assertRaises(ExtractionIntegrityError):
                self._extract(fixture, max_pending_relation_sources=1)

    def test_extraction_is_bound_to_inspected_evidence_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _SessionFixture(Path(tmp))
            events = [
                _request(
                    0,
                    request_id="r1",
                    path="/units/42/save",
                    query={"id": ["before"]},
                )
            ]
            fixture.write(events)
            reader = fixture.reader()
            identity = reader.inspect(SESSION_ID)

            events[0]["query"] = {"id": ["after"]}
            fixture.write(events)
            extractor = ObservationExtractor(_contract(), reader, RedactionPolicy.default())
            with self.assertRaises(EvidenceIntegrityError):
                extractor.extract(identity)


if __name__ == "__main__":
    unittest.main()
