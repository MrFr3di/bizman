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


if __name__ == "__main__":
    unittest.main()
