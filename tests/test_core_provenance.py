from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from bizman.core import (
    CoreContext,
    DataIntegrityError,
    EvidenceTrace,
    EvidenceTraceRequest,
    RepositoryAssets,
    SystemUtcClock,
    trace_evidence,
    validate_repository,
)
from bizman.readmodel import project_curated_knowledge


REPO_ROOT = Path(__file__).resolve().parents[1]
HAR_REF = "src.har.bizmania.2026-09-06.01#entry-224"
LIVE_REF = "live-cdp-2026-09-07#seq-25730"
WEBCOPY_REF = "src.webcopy.bizmania.2026-10-03.01#entry-1001"


def _context(root: Path, data_dir: Path) -> CoreContext:
    return CoreContext(
        assets=RepositoryAssets(root),
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )


def _copy_repository_assets(destination: Path) -> Path:
    destination.mkdir(parents=True)
    for directory in ("config", "schemas", "knowledge"):
        shutil.copytree(REPO_ROOT / directory, destination / directory)
    return destination


class CoreEvidenceTraceTests(unittest.TestCase):
    def test_known_har_trace_returns_bounded_verified_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = trace_evidence(
                _context(REPO_ROOT, Path(tmp) / "BizManData"),
                EvidenceTraceRequest(HAR_REF),
            )

        self.assertIsNotNone(result.trace)
        assert result.trace is not None
        trace = result.trace
        self.assertEqual(trace.evidence_ref, HAR_REF)
        self.assertEqual(trace.source_id, "src.har.bizmania.2026-09-06.01")
        self.assertEqual(trace.source_kind, "har_capture")
        self.assertEqual(trace.locator_kind, "entry")
        self.assertEqual(trace.ordinal, 224)
        self.assertEqual(trace.source_record_count, 10210)
        self.assertFalse(trace.raw_source_committed)
        self.assertEqual(
            trace.source_sha256,
            "55459efda3f6c7cc1e823a97afb69aa5cf8c719f05bb7e6f3fd4f1069d59a79b",
        )
        self.assertIsNone(trace.runtime_session_id)
        self.assertEqual(trace.observed_from, "2026-09-06T11:31:14.202Z")
        self.assertEqual(trace.observed_to, "2026-09-06T11:37:53.704Z")
        self.assertIn("private-source", trace.privacy)
        self.assertIn("Raw HAR files are not committed", trace.provenance_policy)

    def test_known_promoted_session_trace_returns_registered_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = trace_evidence(
                _context(REPO_ROOT, Path(tmp) / "BizManData"),
                EvidenceTraceRequest(LIVE_REF),
            )

        self.assertIsNotNone(result.trace)
        assert result.trace is not None
        trace = result.trace
        self.assertEqual(trace.source_id, "live-cdp-2026-09-07")
        self.assertEqual(trace.source_kind, "promoted_session")
        self.assertEqual(trace.locator_kind, "sequence")
        self.assertEqual(trace.ordinal, 25730)
        self.assertEqual(trace.source_record_count, 32128)
        self.assertFalse(trace.raw_source_committed)
        self.assertIsNone(trace.source_sha256)
        self.assertEqual(
            trace.runtime_session_id,
            "01a07d18-cf20-7207-bbf7-d5fa8a87f103",
        )
        self.assertEqual(trace.observed_from, "2026-09-07T18:19:33Z")
        self.assertIsNone(trace.observed_to)
        self.assertIn("external sanitized collector evidence", trace.privacy)
        self.assertIn("Historical runtime source bytes", trace.provenance_policy)

    def test_known_webcopy_trace_returns_source_level_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = trace_evidence(
                _context(REPO_ROOT, Path(tmp) / "BizManData"),
                EvidenceTraceRequest(WEBCOPY_REF),
            )

        self.assertIsNotNone(result.trace)
        assert result.trace is not None
        trace = result.trace
        self.assertEqual(trace.source_id, "src.webcopy.bizmania.2026-10-03.01")
        self.assertEqual(trace.source_kind, "webcopy_snapshot")
        self.assertEqual(trace.locator_kind, "entry")
        self.assertEqual(trace.ordinal, 1001)
        self.assertEqual(trace.source_record_count, 3130)
        self.assertFalse(trace.raw_source_committed)
        self.assertEqual(
            trace.source_sha256,
            "f47fe0330ded4e4802f905f53498db448e734f943e52a90a4a6560e31d6d74da",
        )
        self.assertIsNone(trace.runtime_session_id)
        self.assertEqual(trace.observed_from, "2026-10-03T16:32:00+03:00")
        self.assertEqual(trace.observed_to, "2026-10-03T16:57:00+03:00")
        self.assertIn("documentation pages", trace.privacy)
        self.assertIn("origin-index SHA-256", trace.provenance_policy)

    def test_unknown_or_out_of_range_evidence_returns_no_trace(self):
        refs = (
            "unknown.source#entry-1",
            "src.har.bizmania.2026-09-06.01#entry-10210",
            "src.har.bizmania.2026-09-06.01#seq-1",
            "live-cdp-2026-09-07#seq-32128",
            "live-cdp-2026-09-07#entry-1",
            "src.webcopy.bizmania.2026-10-03.01#entry-3130",
            "src.webcopy.bizmania.2026-10-03.01#seq-1",
        )
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(REPO_ROOT, Path(tmp) / "BizManData")
            for evidence_ref in refs:
                with self.subTest(evidence_ref=evidence_ref):
                    result = trace_evidence(
                        context,
                        EvidenceTraceRequest(evidence_ref),
                    )
                    self.assertIsNone(result.trace)

    def test_malformed_evidence_ref_fails_at_request_boundary(self):
        for value in (
            "",
            "src.har.bizmania.2026-09-06.01",
            "src.har.bizmania.2026-09-06.01#entry--1",
            "src.har.bizmania.2026-09-06.01#entry-01",
            "../source#entry-1",
        ):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                EvidenceTraceRequest(value)

    def test_all_projected_knowledge_evidence_refs_are_traceable(self):
        evidence_refs = sorted(
            {
                evidence_ref
                for record in project_curated_knowledge(REPO_ROOT).records
                for evidence_ref in record.evidence_refs
            }
        )
        self.assertTrue(evidence_refs)

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(REPO_ROOT, Path(tmp) / "BizManData")
            missing = [
                evidence_ref
                for evidence_ref in evidence_refs
                if trace_evidence(
                    context,
                    EvidenceTraceRequest(evidence_ref),
                ).trace
                is None
            ]

        self.assertEqual(missing, [])

    def test_capture_registry_manifest_disagreement_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _copy_repository_assets(Path(tmp) / "repo")
            captures_path = root / "knowledge" / "sources" / "captures.json"
            document = json.loads(captures_path.read_text(encoding="utf-8"))
            document["captures"][0]["sha256"] = "f" * 64
            captures_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            context = _context(root, Path(tmp) / "BizManData")

            with self.assertRaisesRegex(DataIntegrityError, "identity mismatch"):
                trace_evidence(context, EvidenceTraceRequest(HAR_REF))

    def test_invalid_promoted_uuidv7_fails_runtime_and_repository_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _copy_repository_assets(Path(tmp) / "repo")
            promoted_path = root / "knowledge" / "sources" / "promoted-sessions.json"
            document = json.loads(promoted_path.read_text(encoding="utf-8"))
            document["sources"][0]["runtime_session_id"] = (
                "01a07d18-cf20-4207-bbf7-d5fa8a87f103"
            )
            promoted_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            context = _context(root, Path(tmp) / "BizManData")

            validation = validate_repository(context)
            self.assertFalse(validation.ok)
            self.assertTrue(
                any("promoted-session-index.schema.json" in error for error in validation.errors)
            )
            with self.assertRaisesRegex(DataIntegrityError, "UUIDv7"):
                trace_evidence(context, EvidenceTraceRequest(LIVE_REF))

    def test_invalid_webcopy_digest_fails_repository_and_runtime_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _copy_repository_assets(Path(tmp) / "repo")
            manifest_path = (
                root
                / "knowledge"
                / "sources"
                / "webcopy-bizmania.2026-10-03.01.json"
            )
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["origin_index_sha256"] = "not-a-digest"
            manifest_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            context = _context(root, Path(tmp) / "BizManData")

            validation = validate_repository(context)
            self.assertFalse(validation.ok)
            self.assertTrue(
                any(
                    "invalid webcopy origin_index_sha256" in error
                    for error in validation.errors
                )
            )
            with self.assertRaisesRegex(
                DataIntegrityError,
                "invalid SHA-256",
            ):
                trace_evidence(context, EvidenceTraceRequest(WEBCOPY_REF))

    def test_webcopy_source_id_collision_fails_repository_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _copy_repository_assets(Path(tmp) / "repo")
            manifest_path = (
                root
                / "knowledge"
                / "sources"
                / "webcopy-bizmania.2026-10-03.01.json"
            )
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["id"] = "src.har.bizmania.2026-09-06.01"
            manifest_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            context = _context(root, Path(tmp) / "BizManData")

            validation = validate_repository(context)
            self.assertFalse(validation.ok)
            self.assertTrue(
                any(
                    "duplicate provenance source id" in error
                    for error in validation.errors
                )
            )

    def test_provenance_dtos_are_frozen_slotted_and_path_free(self):
        for dto in (EvidenceTraceRequest, EvidenceTrace):
            self.assertIn("__slots__", dto.__dict__)
            self.assertTrue(dto.__dataclass_params__.frozen)
            self.assertTrue(
                all("Path" not in str(field.type) for field in fields(dto)),
                dto.__name__,
            )

        request = EvidenceTraceRequest(HAR_REF)
        with self.assertRaises(FrozenInstanceError):
            request.evidence_ref = LIVE_REF

        with self.assertRaisesRegex(ValueError, "RFC3339"):
            EvidenceTrace(
                evidence_ref=HAR_REF,
                source_id="src.har.bizmania.2026-09-06.01",
                source_kind="har_capture",
                locator_kind="entry",
                ordinal=224,
                source_record_count=10210,
                raw_source_committed=False,
                source_sha256="a" * 64,
                runtime_session_id=None,
                observed_from="not-a-time",
                observed_to=None,
                privacy="private",
                provenance_policy="policy",
            )

        with self.assertRaisesRegex(ValueError, "source does not match"):
            EvidenceTrace(
                evidence_ref=HAR_REF,
                source_id="src.har.bizmania.2026-09-06.02",
                source_kind="har_capture",
                locator_kind="entry",
                ordinal=224,
                source_record_count=10210,
                raw_source_committed=False,
                source_sha256="a" * 64,
                runtime_session_id=None,
                observed_from="2026-09-06T11:31:14.202Z",
                observed_to="2026-09-06T11:37:53.704Z",
                privacy="private",
                provenance_policy="policy",
            )


if __name__ == "__main__":
    unittest.main()
