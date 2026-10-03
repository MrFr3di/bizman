from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "bizman"


def _direct_internal_import_violations(
    package: str,
    forbidden: set[str],
) -> list[str]:
    violations: list[str] = []
    for path in sorted((SRC_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if any(
                    module == root or module.startswith(root + ".")
                    for root in forbidden
                ):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}:{module}"
                    )
    return violations


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
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Sessions do not depend on higher layers": {
                "source_modules": ["bizman.sessions"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Collector is independent from detector and application layers": {
                "source_modules": ["bizman.collector"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Changes do not depend on collector or application layers": {
                "source_modules": ["bizman.changes"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Read model is independent from collector and application layers": {
                "source_modules": ["bizman.readmodel"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Current State depends only on deterministic lower layers": {
                "source_modules": ["bizman.current"],
                "forbidden_modules": [
                    "bizman.collector",
                    "bizman.readmodel",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
            },
            "Core does not depend on adapters": {
                "source_modules": ["bizman.core"],
                "forbidden_modules": ["bizman.cli", "bizman.mcp", "bizman.telegram"],
            },
            "CLI directly consumes Core only": {
                "source_modules": ["bizman.cli"],
                "forbidden_modules": [
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
                "allow_indirect_imports": True,
            },
            "MCP directly consumes Core only": {
                "source_modules": ["bizman.mcp"],
                "forbidden_modules": [
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.cli",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
                "allow_indirect_imports": True,
            },
            "Telegram directly consumes Core only": {
                "source_modules": ["bizman.telegram"],
                "forbidden_modules": [
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.cli",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                ],
                "allow_indirect_imports": True,
            },
            "Market depends only on deterministic lower layers": {
                "source_modules": ["bizman.market"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.experiments",
                    "bizman.ingest",
                    "bizman.mcp",
                    "bizman.telegram",
                ],
            },
            "Experiments depend only on deterministic lower layers": {
                "source_modules": ["bizman.experiments"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.market",
                    "bizman.ingest",
                    "bizman.mcp",
                    "bizman.telegram",
                ],
            },
            "Ingest depends only on foundation": {
                "source_modules": ["bizman.ingest"],
                "forbidden_modules": [
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.mcp",
                    "bizman.telegram",
                ],
            },
        }
        self.assertEqual(set(contracts), set(expected))
        for name, contract_expected in expected.items():
            contract = contracts[name]
            self.assertEqual(contract["type"], "forbidden", name)
            for key, value in contract_expected.items():
                self.assertEqual(value, contract[key], f"{name}: {key}")

    def test_cli_has_no_direct_internal_package_imports(self):
        self.assertEqual(
            _direct_internal_import_violations(
                "cli",
                {
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                },
            ),
            [],
        )

    def test_current_state_has_no_direct_adapter_readmodel_or_collector_imports(self):
        self.assertEqual(
            _direct_internal_import_violations(
                "current",
                {
                    "bizman.collector",
                    "bizman.readmodel",
                    "bizman.core",
                    "bizman.cli",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                },
            ),
            [],
        )

    def test_mcp_has_no_direct_lower_layer_or_cli_imports(self):
        self.assertEqual(
            _direct_internal_import_violations(
                "mcp",
                {
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.cli",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                },
            ),
            [],
        )

    def test_telegram_has_no_direct_lower_layer_or_cli_imports(self):
        self.assertEqual(
            _direct_internal_import_violations(
                "telegram",
                {
                    "bizman.foundation",
                    "bizman.sessions",
                    "bizman.collector",
                    "bizman.changes",
                    "bizman.readmodel",
                    "bizman.current",
                    "bizman.cli",
                    "bizman.mcp",
                    "bizman.market",
                    "bizman.experiments",
                    "bizman.ingest",
                },
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
