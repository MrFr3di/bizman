from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import AnalysisProfile, Finding
from tools.bizman_detector.state import DetectorState


SESSION_ID = "01991c7d-a400-7000-8000-000000000001"
EVENT_ID = "01991c7d-a400-7000-8000-000000000011"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _identity() -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=SESSION_ID,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        started_at="2026-09-07T12:00:00Z",
        ended_at="2026-09-07T12:01:00Z",
        status="completed",
    )


def _profile() -> AnalysisProfile:
    return AnalysisProfile(
        baseline_sha256=SHA_D,
        contract_schema_version=1,
        normalization_version=1,
        extraction_version=1,
        redaction_policy_sha256=SHA_E,
        rules=(),
        sha256=SHA_C,
    )


def _finding(
    *,
    change_id: str = "chg." + "1" * 64,
    rule_id: str = "BM-HTTP-003",
    kind: str = "endpoint.query_key_added",
    novelty_class: str = "novel",
    subject=(("method", "GET"), ("path", "/x")),
    delta=(("added_keys", ("q",)),),
) -> Finding:
    return Finding(
        change_id=change_id,
        rule_id=rule_id,
        rule_version=1,
        kind=kind,
        novelty_class=novelty_class,
        subject=subject,
        delta=delta,
        evidence_event_ids=(EVENT_ID,),
    )


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "promotion-bundle.schema.json"


def _sample_document() -> dict:
    return {
        "schema_version": "1.0",
        "bundle_id": "promotion." + "2" * 64,
        "analysis_profile_sha256": SHA_C,
        "baseline_sha256": SHA_D,
        "redaction_policy_sha256": SHA_E,
        "session_id": SESSION_ID,
        "evidence_sha256": SHA_B,
        "manifest_sha256": SHA_A,
        "created_at": "2026-09-07T12:01:00Z",
        "detector": {
            "normalization_version": 1,
            "extraction_version": 1,
        },
        "findings": [
            {
                "change_id": "chg." + "1" * 64,
                "rule_id": "BM-HTTP-003",
                "rule_version": 1,
                "kind": "endpoint.query_key_added",
                "novelty_class": "novel",
                "evidence_confidence": "observed",
                "subject": {"method": "GET", "path": "/x"},
                "delta": {"added_keys": ["q"]},
                "evidence_event_ids": [EVENT_ID],
            }
        ],
    }


class PromotionSchemaTests(unittest.TestCase):
    def test_schema_is_draft_2020_12_closed_and_accepts_expected_bundle(self):
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.assertEqual(list(validator.iter_errors(_sample_document())), [])

        for mutated in (
            {**_sample_document(), "unexpected": True},
            {
                **_sample_document(),
                "detector": {**_sample_document()["detector"], "unexpected": True},
            },
            {
                **_sample_document(),
                "findings": [
                    {**_sample_document()["findings"][0], "unexpected": True}
                ],
            },
        ):
            self.assertTrue(list(validator.iter_errors(mutated)))


class PromotionBuilderTests(unittest.TestCase):
    def test_builder_is_deterministic_profile_aware_and_uses_session_end_time(self):
        from tools.bizman_detector.promotion import PromotionBundleBuilder

        builder = PromotionBundleBuilder(_schema_path())
        identity = _identity()
        profile = _profile()
        first = _finding()
        second = _finding(
            change_id="chg." + "0" * 64,
            rule_id="BM-HTTP-004",
            kind="endpoint.status_added",
            delta=(("status", 418),),
        )

        left = builder.build(identity, profile, (first, second))
        right = builder.build(identity, profile, (second, first))
        self.assertEqual(left, right)

        document = json.loads(left.payload_json)
        self.assertEqual(document["created_at"], identity.ended_at)
        self.assertEqual(
            [item["change_id"] for item in document["findings"]],
            sorted([first.change_id, second.change_id]),
        )
        without_id = dict(document)
        bundle_id = without_id.pop("bundle_id")
        from tools.bizman_foundation.fingerprint import canonical_sha256

        self.assertEqual(bundle_id, "promotion." + canonical_sha256(without_id))
        self.assertEqual(
            left.payload_sha256,
            hashlib.sha256(left.payload_json.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(
            left.bundle_id,
            builder.build(
                identity,
                AnalysisProfile(
                    baseline_sha256=profile.baseline_sha256,
                    contract_schema_version=profile.contract_schema_version,
                    normalization_version=profile.normalization_version,
                    extraction_version=profile.extraction_version,
                    redaction_policy_sha256=profile.redaction_policy_sha256,
                    rules=profile.rules,
                    sha256="f" * 64,
                ),
                (first, second),
            ).bundle_id,
        )

    def test_builder_fails_closed_on_value_bearing_or_sensitive_semantics(self):
        from tools.bizman_detector.promotion import PromotionBundleBuilder, PromotionPrivacyError

        builder = PromotionBundleBuilder(_schema_path())
        forbidden = (
            _finding(delta=(("query_value", "synthetic-query-secret"),)),
            _finding(delta=(("body_value", "synthetic-body-secret"),)),
            _finding(delta=(("form_value", "synthetic-form-secret"),)),
            _finding(delta=(("added_keys", ("password",)),)),
        )
        for finding in forbidden:
            with self.subTest(delta=finding.delta):
                with self.assertRaises(PromotionPrivacyError):
                    builder.build(_identity(), _profile(), (finding,))


class PromotionMaterializerTests(unittest.TestCase):
    def _pending_state(self, root: Path):
        from tools.bizman_detector.promotion import PromotionBundleBuilder

        state = DetectorState.open_rw(root / "detector" / "state.sqlite3")
        builder = PromotionBundleBuilder(_schema_path())
        result = state.process_session_transaction(
            identity=_identity(),
            profile=_profile(),
            findings=(_finding(),),
            outbox_factory=builder.build,
            processed_at="2026-09-07T12:02:00Z",
        )
        self.assertTrue(result.processed)
        self.assertIsNotNone(result.outbox_bundle_id)
        return state, builder

    def test_pending_row_materializes_atomically_and_is_marked(self):
        from tools.bizman_detector.promotion import PromotionMaterializer

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, _ = self._pending_state(root)
            self.addCleanup(state.close)
            pending = state.pending_outbox()[0]
            materializer = PromotionMaterializer(root, state, _schema_path())
            paths = materializer.materialize_pending(materialized_at="2026-09-07T12:03:00Z")

            expected = (
                root
                / "promotions"
                / pending.analysis_profile_sha256[:16]
                / pending.session_id
                / f"{pending.bundle_id}.json"
            )
            self.assertEqual(paths, (expected,))
            self.assertEqual(expected.read_text(encoding="utf-8"), pending.payload_json)
            self.assertEqual(state.pending_outbox(), ())
            self.assertEqual(list(expected.parent.glob("*.tmp")), [])

    def test_existing_identical_file_is_accepted_and_different_bytes_fail_closed(self):
        from tools.bizman_detector.promotion import PromotionIntegrityError, PromotionMaterializer

        for identical in (True, False):
            with self.subTest(identical=identical), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state, _ = self._pending_state(root)
                try:
                    pending = state.pending_outbox()[0]
                    materializer = PromotionMaterializer(root, state, _schema_path())
                    target = materializer.path_for(pending)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(
                        pending.payload_json.encode("utf-8") if identical else b"{}"
                    )
                    if identical:
                        self.assertEqual(
                            materializer.materialize_pending(
                                materialized_at="2026-09-07T12:03:00Z"
                            ),
                            (target,),
                        )
                        self.assertEqual(state.pending_outbox(), ())
                    else:
                        with self.assertRaises(PromotionIntegrityError):
                            materializer.materialize_pending(
                                materialized_at="2026-09-07T12:03:00Z"
                            )
                        self.assertEqual(len(state.pending_outbox()), 1)
                        self.assertEqual(target.read_bytes(), b"{}")
                finally:
                    state.close()


if __name__ == "__main__":
    unittest.main()
