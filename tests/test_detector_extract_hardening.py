from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

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


def _event(sequence: int, *, source: str, event_type: str, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": _uuid7(sequence + 100),
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": source,
        "event_type": event_type,
        "confidence": "inferred" if source == "system" else "observed",
    }
    value.update(extra)
    return value


def _request(sequence: int, *, path: str, redirect_index: int = 0) -> dict[str, object]:
    return _event(
        sequence,
        source="cdp.network",
        event_type="http.request",
        request_id="r1",
        redirect_index=redirect_index,
        method="POST",
        url_path=path,
        route_pattern=None,
        status_code=None,
        query={"id": ["value"]},
        headers={},
        request_body_ref=None,
        response_body_ref=None,
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
    )


def _action(sequence: int, *, path: str) -> dict[str, object]:
    return _event(
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
        form_field_names=["safe"],
    )


def _link(sequence: int, *, action_id: str, request_id: str) -> dict[str, object]:
    return _event(
        sequence,
        source="system",
        event_type="correlation.action_http",
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[action_id],
        action_event_id=action_id,
        network_event_id=request_id,
        correlation_status="strong",
        correlation_score=0.9,
        delta_ms=10.0,
        signals=["form-path-match"],
    )


class _Fixture:
    def __init__(self, root: Path):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.data_dir = root / "BizManData"
        self.event_rel = f"events/2026-09-07/{SESSION_ID}.jsonl"
        self.event_path = self.data_dir / self.event_rel
        self.manifest_path = self.data_dir / "sessions" / SESSION_ID / "manifest.json"

    def write(self, events: list[dict[str, object]]) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self.event_path.write_bytes(
            b"".join(
                (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for event in events
            )
        )
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "session_id": SESSION_ID,
                    "started_at": "2026-09-07T12:00:00Z",
                    "ended_at": "2026-09-07T12:01:00Z",
                    "status": "completed",
                    "collector": {"name": "bizman-cdp", "version": "0.2.1"},
                    "browser": {"product": "Chrome", "version": "140.0"},
                    "protocol": {
                        "name": "cdp",
                        "version": "1.3",
                        "sha256": None,
                        "artifact_ref": None,
                    },
                    "event_files": [self.event_rel],
                    "artifact_count": 0,
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

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


class ExtractionHardeningTests(unittest.TestCase):
    def test_iter_events_can_bind_actual_stream_bytes_to_expected_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _Fixture(Path(tmp))
            events = [_request(0, path="/units/42/save")]
            fixture.write(events)
            reader = fixture.reader()
            identity = reader.inspect(SESSION_ID)

            events[0]["query"] = {"id": ["value"], "novel": ["changed"]}
            fixture.write(events)
            with self.assertRaises(EvidenceIntegrityError):
                list(reader.iter_events(SESSION_ID, expected_identity=identity))

    def test_redirect_status_is_attached_to_previous_request_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _Fixture(Path(tmp))
            first = _request(0, path="/units/42/save", redirect_index=0)
            second = _request(1, path="/units/43/save", redirect_index=1)
            second["redirect_from_path"] = "/units/42/save"
            second["redirect_status_code"] = 302
            fixture.write([first, second])
            reader = fixture.reader()
            identity = reader.inspect(SESSION_ID)
            observations = ObservationExtractor(
                _contract(), reader, RedactionPolicy.default()
            ).extract(identity)
            self.assertEqual([item.status for item in observations.http], [302, None])

    def test_partial_redirect_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _Fixture(Path(tmp))
            first = _request(0, path="/units/42/save", redirect_index=0)
            second = _request(1, path="/units/43/save", redirect_index=1)
            second["redirect_status_code"] = 302
            fixture.write([first, second])
            reader = fixture.reader()
            identity = reader.inspect(SESSION_ID)
            with self.assertRaisesRegex(ExtractionIntegrityError, "redirect metadata"):
                ObservationExtractor(
                    _contract(), reader, RedactionPolicy.default()
                ).extract(identity)

    def test_correlation_action_refs_must_agree_with_action_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _Fixture(Path(tmp))
            action = _action(0, path="/units/42/save")
            request = _request(1, path="/units/42/save")
            link = _link(
                2,
                action_id=str(action["event_id"]),
                request_id=str(request["event_id"]),
            )
            link["action_refs"] = [_uuid7(999)]
            fixture.write([action, request, link])
            reader = fixture.reader()
            identity = reader.inspect(SESSION_ID)
            with self.assertRaises(ExtractionIntegrityError):
                ObservationExtractor(
                    _contract(), reader, RedactionPolicy.default()
                ).extract(identity)


if __name__ == "__main__":
    unittest.main()
