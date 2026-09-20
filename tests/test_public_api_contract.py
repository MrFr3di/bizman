from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "bizman"


class PackageDependencyArchitectureTests(unittest.TestCase):
    def test_src_package_never_imports_tools_namespace(self):
        violations: list[str] = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "tools" or alias.name.startswith("tools."):
                            violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "tools" or module.startswith("tools."):
                        violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_import_linter_contracts_match_design_dependency_directions(self):
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        config = data["tool"]["importlinter"]
        self.assertEqual(config["root_package"], "bizman")

        contracts = {contract["name"]: contract for contract in config["contracts"]}
        expected = {
            "Foundation is a dependency leaf": {
                "source_modules": ["bizman.foundation"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                ],
            },
            "Sessions do not depend on higher layers": {
                "source_modules": ["bizman.sessions"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                ],
            },
            "Collector is independent from detector and application layers": {
                "source_modules": ["bizman.collector"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                ],
            },
            "Changes do not depend on collector or application layers": {
                "source_modules": ["bizman.changes"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.core",
                    "bizman.cli",
                ],
            },
            "Core does not depend on CLI": {
                "source_modules": ["bizman.core"],
                "forbidden_modules": ["bizman.cli"],
            },
            "CLI directly consumes Core only": {
                "source_modules": ["bizman.cli"],
                "forbidden_modules": [
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                ],
                "allow_indirect_imports": True,
            },
        }
        self.assertEqual(set(contracts), set(expected))
        for name, contract_expected in expected.items():
            contract = contracts[name]
            self.assertEqual(contract["type"], "forbidden", name)
            for key, value in contract_expected.items():
                self.assertEqual(contract[key], value, f"{name}: {key}")

    def test_cli_has_no_direct_internal_package_imports(self):
        forbidden = {
            "bizman.foundation",
            "bizman.sessions",
            "bizman.collector",
            "bizman.changes",
        }
        violations: list[str] = []
        for path in sorted((SRC_ROOT / "cli").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                for module in modules:
                    if any(module == root or module.startswith(root + ".") for root in forbidden):
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno}:{module}"
                        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
