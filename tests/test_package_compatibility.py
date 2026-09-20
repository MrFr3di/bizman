from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FoundationPackageCompatibilityTests(unittest.TestCase):
    def test_legacy_foundation_exports_are_canonical_package_objects(self):
        from bizman.foundation.fingerprint import canonical_sha256 as canonical_new
        from bizman.foundation.redaction import RedactionPolicy as policy_new
        from bizman.foundation.session import new_uuid7 as uuid_new
        from bizman.foundation.validation import ValidationResult as result_new
        from tools.bizman_foundation.fingerprint import canonical_sha256 as canonical_old
        from tools.bizman_foundation.redaction import RedactionPolicy as policy_old
        from tools.bizman_foundation.session import new_uuid7 as uuid_old
        from tools.bizman_foundation.validation import ValidationResult as result_old

        self.assertIs(canonical_old, canonical_new)
        self.assertIs(policy_old, policy_new)
        self.assertIs(uuid_old, uuid_new)
        self.assertIs(result_old, result_new)


class SessionsPackageCompatibilityTests(unittest.TestCase):
    def test_legacy_evidence_exports_are_canonical_sessions_objects(self):
        from bizman.sessions.evidence import (
            EvidenceError as error_new,
            EvidenceIdentity as identity_new,
            EvidenceIntegrityError as integrity_new,
            EvidenceReader as reader_new,
        )
        from bizman.sessions.status import EvidenceSessionStatus as status_new
        from tools.bizman_detector.evidence import (
            EvidenceError as error_old,
            EvidenceIdentity as identity_old,
            EvidenceIntegrityError as integrity_old,
            EvidenceReader as reader_old,
        )
        from tools.bizman_detector.session_status import EvidenceSessionStatus as status_old

        self.assertIs(error_old, error_new)
        self.assertIs(identity_old, identity_new)
        self.assertIs(integrity_old, integrity_new)
        self.assertIs(reader_old, reader_new)
        self.assertIs(status_old, status_new)


class CollectorPackageCompatibilityTests(unittest.TestCase):
    def test_legacy_collector_exports_are_canonical_package_objects(self):
        from bizman.collector.cdp import CdpConnection as cdp_new
        from bizman.collector.network import NetworkNormalizer as network_new
        from bizman.collector.runtime import (
            CollectorEventPipeline as pipeline_new,
            run_collection as run_new,
        )
        from bizman.collector.storage import SessionWriter as writer_new
        from tools.bizman_collector.cdp import CdpConnection as cdp_old
        from tools.bizman_collector.network import NetworkNormalizer as network_old
        from tools.bizman_collector.runtime import (
            CollectorEventPipeline as pipeline_old,
            run_collection as run_old,
        )
        from tools.bizman_collector.storage import SessionWriter as writer_old

        self.assertIs(cdp_old, cdp_new)
        self.assertIs(network_old, network_new)
        self.assertIs(pipeline_old, pipeline_new)
        self.assertIs(run_old, run_new)
        self.assertIs(writer_old, writer_new)


class ChangesPackageCompatibilityTests(unittest.TestCase):
    def test_legacy_detector_exports_are_canonical_changes_objects(self):
        from bizman.changes.baseline import BaselineCompiler as compiler_new
        from bizman.changes.diff import SemanticDiff as diff_new
        from bizman.changes.extract import ObservationExtractor as extractor_new
        from bizman.changes.promotion import PromotionBundleBuilder as promotion_new
        from bizman.changes.rules import RuleEngine as rules_new
        from bizman.changes.runner import DetectorRunner as runner_new
        from bizman.changes.state import DetectorState as state_new
        from tools.bizman_detector.baseline import BaselineCompiler as compiler_old
        from tools.bizman_detector.diff import SemanticDiff as diff_old
        from tools.bizman_detector.extract import ObservationExtractor as extractor_old
        from tools.bizman_detector.promotion import PromotionBundleBuilder as promotion_old
        from tools.bizman_detector.rules import RuleEngine as rules_old
        from tools.bizman_detector.runner import DetectorRunner as runner_old
        from tools.bizman_detector.state import DetectorState as state_old

        self.assertIs(compiler_old, compiler_new)
        self.assertIs(diff_old, diff_new)
        self.assertIs(extractor_old, extractor_new)
        self.assertIs(promotion_old, promotion_new)
        self.assertIs(rules_old, rules_new)
        self.assertIs(runner_old, runner_new)
        self.assertIs(state_old, state_new)


if __name__ == "__main__":
    unittest.main()
