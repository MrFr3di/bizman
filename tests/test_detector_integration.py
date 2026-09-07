from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from tools.bizman_detector.baseline import BaselineCompiler, build_analysis_profile
from tools.bizman_detector.diff import SemanticDiff
from tools.bizman_detector.evidence import EvidenceReader
from tools.bizman_detector.extract import ObservationExtractor
from tools.bizman_detector.promotion import PromotionBundleBuilder
from tools.bizman_detector.rules import RULE_DESCRIPTORS, RuleEngine
from tools.bizman_detector.runner import DetectorRunner
from tools.bizman_detector.state import DetectorState
from tools.bizman_foundation.redaction import load_redaction_policy


SESSION_ID = "01991c7d-a400-7000-8000-000000000001"
QUERY_SECRET = "SYNTHETIC-QUERY-SECRET-8a0c7d"
BODY_SECRET = "SYNTHETIC-BODY-SECRET-4d7b3e"
FORM_SECRET = "SYNTHETIC-FORM-SECRET-92f1aa"
FIXED_CLOCK = "2026-09-07T12:02:00Z"
POST_PATH = "/units/customise/headquarterretailsettings/"
GET_PATH = "/analitics/vendors/"


def _uuid7(index: int) -> str:
    return f"01991c7d-a400-7000-8000-{index:012x}"


def _base_event(
    sequence: int,
    *,
    source: str,
    event_type: str,
    confidence: str = "observed",
    **extra: object,
) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": _uuid7(sequence + 16),
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": source,
        "event_type": event_type,
        "confidence": confidence,
    }
    event.update(extra)
    return event


def _request(
    sequence: int,
    *,
    request_id: str,
    method: str,
    path: str,
    query: dict[str, list[str]],
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
        query=query,
        headers=headers,
        request_body_ref=body_ref,
        response_body_ref=None,
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
    )


def _response(
    sequence: int,
    *,
    request_id: str,
    method: str,
    path: str,
    status: int = 200,
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="cdp.network",
        event_type="http.response",
        request_id=request_id,
        redirect_index=0,
        method=method,
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


def _action(sequence: int) -> dict[str, object]:
    return _base_event(
        sequence,
        source="dom.action",
        event_type="dom.action",
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[],
        action_kind="submit",
        is_trusted=True,
        page_path=POST_PATH,
        element_tag="form",
        element_type=None,
        element_name=None,
        element_role=None,
        safe_selector="form#retail-settings",
        form_action_path=POST_PATH,
        form_method="POST",
        form_field_names=[
            "cityId",
            "autoSupplyReserve",
            "autoRetailModifier",
            "$post",
            f"password_{FORM_SECRET}",
        ],
    )


def _correlation(
    sequence: int,
    *,
    action_event_id: str,
    request_event_id: str,
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="system",
        event_type="correlation.action_http",
        confidence="inferred",
        target_id="target-1",
        frame_id="frame-1",
        action_refs=[action_event_id],
        action_event_id=action_event_id,
        network_event_id=request_event_id,
        correlation_status="strong",
        correlation_score=0.95,
        delta_ms=20.0,
        signals=["form-path-match", "method-match"],
    )


class _IntegrationFixture:
    def __init__(self, root: Path) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.data_dir = root / "BizManData"
        self.event_rel = f"events/2026-09-07/{SESSION_ID}.jsonl"
        self.event_path = self.data_dir / self.event_rel
        self.manifest_path = self.data_dir / "sessions" / SESSION_ID / "manifest.json"

    def _put_artifact(self, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.data_dir / "artifacts" / "sha256" / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return f"sha256:{digest}"

    def write(self) -> None:
        body_ref = self._put_artifact(
            json.dumps(
                {
                    "$post": BODY_SECRET,
                    "autoRetailModifier": BODY_SECRET,
                    "autoSupplyReserve": BODY_SECRET,
                    "cityId": BODY_SECRET,
                    "password": BODY_SECRET,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        action = _action(2)
        post_request = _request(
            3,
            request_id="post-known-body",
            method="POST",
            path=POST_PATH,
            query={"id": [QUERY_SECRET], "mode": [QUERY_SECRET]},
            body_ref=body_ref,
            content_type="application/json; charset=utf-8",
        )
        events = [
            _request(
                0,
                request_id="known-get",
                method="GET",
                path=GET_PATH,
                query={},
            ),
            _response(1, request_id="known-get", method="GET", path=GET_PATH),
            action,
            post_request,
            _response(
                4,
                request_id="post-known-body",
                method="POST",
                path=POST_PATH,
            ),
            _correlation(
                5,
                action_event_id=str(action["event_id"]),
                request_event_id=str(post_request["event_id"]),
            ),
            _request(
                6,
                request_id="post-unknown-body",
                method="POST",
                path=POST_PATH,
                query={"id": [QUERY_SECRET]},
                body_ref=None,
            ),
            _response(
                7,
                request_id="post-unknown-body",
                method="POST",
                path=POST_PATH,
            ),
        ]
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self.event_path.write_bytes(
            b"".join(
                (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode(
                    "utf-8"
                )
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
            "protocol": {
                "name": "cdp",
                "version": "1.3",
                "sha256": None,
                "artifact_ref": None,
            },
            "event_files": [self.event_rel],
            "artifact_count": 1,
            "warnings": [],
        }
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def redaction(self):
        return load_redaction_policy(self.repo_root / "config" / "redaction-policy.json")

    @property
    def state_path(self) -> Path:
        return self.data_dir / "detector" / "state.sqlite3"

    def run(self):
        runner = DetectorRunner.from_paths(
            repo_root=self.repo_root,
            data_dir=self.data_dir,
            redaction=self.redaction(),
            clock=lambda: FIXED_CLOCK,
        )
        with runner:
            return runner.run()


class DetectorSyntheticIntegrationTests(unittest.TestCase):
    def test_end_to_end_rerun_schema_and_privacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _IntegrationFixture(Path(tmp))
            fixture.write()

            first = fixture.run()
            self.assertEqual(first.sessions_discovered, 1)
            self.assertEqual(first.sessions_processed, 1)
            self.assertEqual(first.sessions_checkpointed, 0)
            self.assertEqual(first.sessions_failed_skipped, 0)
            self.assertEqual(first.sessions_unfinalized_skipped, 0)
            self.assertEqual(first.fact_counts["novel"], 2)
            self.assertEqual(first.fact_counts["indeterminate"], 1)
            self.assertEqual(first.fact_counts["conflict"], 0)
            self.assertEqual(first.materialized_bundle_count, 1)
            self.assertEqual(first.pending_bundle_count, 0)
            self.assertEqual(len(first.first_seen_change_ids), 2)

            bundles = sorted((fixture.data_dir / "promotions").rglob("*.json"))
            self.assertEqual(len(bundles), 1)
            bundle_bytes = bundles[0].read_bytes()
            bundle = json.loads(bundle_bytes.decode("utf-8"))
            schema = json.loads(
                (fixture.repo_root / "schemas" / "promotion-bundle.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(bundle)
            self.assertEqual(
                {finding["rule_id"] for finding in bundle["findings"]},
                {"BM-HTTP-003", "BM-OP-002"},
            )
            self.assertEqual(bundle["created_at"], "2026-09-07T12:01:00Z")

            with closing(sqlite3.connect(fixture.state_path)) as connection:
                change_rows = connection.execute(
                    "SELECT rule_id, occurrence_count, identity_json FROM changes ORDER BY rule_id"
                ).fetchall()
                outbox_rows = connection.execute(
                    "SELECT state, payload_json FROM promotion_outbox"
                ).fetchall()
            self.assertEqual(
                [(str(row[0]), int(row[1])) for row in change_rows],
                [("BM-HTTP-003", 1), ("BM-OP-002", 1)],
            )
            self.assertEqual([str(row[0]) for row in outbox_rows], ["materialized"])

            forbidden = tuple(value.encode("utf-8") for value in (QUERY_SECRET, BODY_SECRET, FORM_SECRET))
            downstream_blobs = [bundle_bytes]
            downstream_blobs.extend(str(row[2]).encode("utf-8") for row in change_rows)
            downstream_blobs.extend(str(row[1]).encode("utf-8") for row in outbox_rows)
            detector_root = fixture.data_dir / "detector"
            downstream_blobs.extend(
                path.read_bytes() for path in detector_root.iterdir() if path.is_file()
            )
            for secret in forbidden:
                for blob in downstream_blobs:
                    self.assertNotIn(secret, blob)

            second = fixture.run()
            self.assertEqual(second.sessions_processed, 0)
            self.assertEqual(second.sessions_checkpointed, 1)
            self.assertEqual(second.first_seen_change_ids, ())
            self.assertEqual(
                set(second.repeated_change_ids), set(first.first_seen_change_ids)
            )
            self.assertEqual(second.materialized_bundle_count, 0)
            self.assertEqual(second.pending_bundle_count, 0)
            self.assertEqual(len(list((fixture.data_dir / "promotions").rglob("*.json"))), 1)

            with closing(sqlite3.connect(fixture.state_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 2
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM promotion_outbox").fetchone()[0], 1
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT DISTINCT occurrence_count FROM changes"
                    ).fetchall(),
                    [(1,)],
                )

    def test_committed_pending_outbox_is_recovered_before_checkpointed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _IntegrationFixture(Path(tmp))
            fixture.write()
            redaction = fixture.redaction()
            compilation = BaselineCompiler.compile(fixture.repo_root, redaction)
            profile = build_analysis_profile(compilation, RULE_DESCRIPTORS)
            reader = EvidenceReader(fixture.repo_root, fixture.data_dir)
            identity = reader.inspect(SESSION_ID)
            observations = ObservationExtractor(
                compilation.contract, reader, redaction
            ).extract(identity)
            facts = SemanticDiff(compilation.contract).compare(observations)
            findings = RuleEngine(
                RULE_DESCRIPTORS,
                normalization_version=profile.normalization_version,
                extraction_version=profile.extraction_version,
            ).apply(facts)
            builder = PromotionBundleBuilder(
                fixture.repo_root / "schemas" / "promotion-bundle.schema.json"
            )

            with DetectorState.open_rw(fixture.state_path) as state:
                result = state.process_session_transaction(
                    identity=identity,
                    profile=profile,
                    findings=findings,
                    outbox_factory=builder.build,
                    processed_at=FIXED_CLOCK,
                )
                self.assertTrue(result.processed)
                self.assertEqual(len(state.pending_outbox()), 1)

            promotions = fixture.data_dir / "promotions"
            self.assertFalse(promotions.exists())

            recovered = fixture.run()
            self.assertEqual(recovered.sessions_processed, 0)
            self.assertEqual(recovered.sessions_checkpointed, 1)
            self.assertEqual(recovered.materialized_bundle_count, 1)
            self.assertEqual(recovered.pending_bundle_count, 0)
            self.assertEqual(len(list(promotions.rglob("*.json"))), 1)

            with closing(sqlite3.connect(fixture.state_path)) as connection:
                states = connection.execute(
                    "SELECT state FROM promotion_outbox"
                ).fetchall()
            self.assertEqual(states, [("materialized",)])


if __name__ == "__main__":
    unittest.main()
