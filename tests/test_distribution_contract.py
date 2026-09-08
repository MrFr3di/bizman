from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class DistributionContractTests(unittest.TestCase):
    def test_pyproject_declares_locked_src_package_contract(self):
        path = REPO_ROOT / "pyproject.toml"
        self.assertTrue(path.is_file(), "pyproject.toml must exist at repository root")
        value = tomllib.loads(path.read_text(encoding="utf-8"))

        project = value["project"]
        self.assertEqual(project["name"], "bizman")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["scripts"], {"bizman": "bizman.cli.main:main"})
        self.assertEqual(
            project["dependencies"],
            [
                "jsonschema[format]>=4.26,<5",
                "websockets>=17.1,<18",
            ],
        )

        build = value["build-system"]
        self.assertEqual(build["build-backend"], "uv_build")
        self.assertEqual(build["requires"], ["uv_build>=0.12.10,<0.13"])

        self.assertEqual(
            value["dependency-groups"]["dev"],
            ["ruff==0.16.3", "import-linter==2.15"],
        )
        self.assertEqual(value["tool"]["uv"]["required-version"], "==0.12.10")
        self.assertEqual(value["tool"]["ruff"]["target-version"], "py311")

    def test_src_package_root_exists(self):
        package = REPO_ROOT / "src" / "bizman" / "__init__.py"
        self.assertTrue(package.is_file(), "src/bizman package root must exist")


if __name__ == "__main__":
    unittest.main()
