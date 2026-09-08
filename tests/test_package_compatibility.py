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


if __name__ == "__main__":
    unittest.main()
