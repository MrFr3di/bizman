from __future__ import annotations

import unittest


class CoreErrorContractTests(unittest.TestCase):
    def test_exact_error_hierarchy_and_exports(self):
        import bizman.core.errors as errors

        expected = {
            "BizManError",
            "ConfigurationError",
            "AssetError",
            "DataIntegrityError",
            "ContractMismatchError",
            "OperationError",
        }
        self.assertEqual(set(errors.__all__), expected)
        self.assertIs(errors.BizManError.__base__, Exception)
        for name in expected - {"BizManError"}:
            error_type = getattr(errors, name)
            self.assertIs(error_type.__base__, errors.BizManError)
            self.assertNotEqual(error_type.__module__, "sqlite3")


if __name__ == "__main__":
    unittest.main()
