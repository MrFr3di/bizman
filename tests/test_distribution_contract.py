from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]


def _relative_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    parts = path.parts
    if parts and parts[0].startswith("bizman-"):
        parts = parts[1:]
    return PurePosixPath(*parts)


def _forbidden_distribution_entry(name: str) -> bool:
    path = _relative_archive_name(name)
    if not path.parts:
        return False
    lowered = tuple(part.casefold() for part in path.parts)
    if lowered[0] in {"tests", "tools"}:
        return True
    if any(part in {"browser-profile", "chrome-profile", "cas"} for part in lowered):
        return True
    filename = lowered[-1]
    if filename == ".env" or filename.startswith(".env."):
        return True
    return any(
        filename.endswith(suffix)
        for suffix in (".db", ".sqlite", ".sqlite3", ".parquet", ".har")
    )


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
        ruff = value["tool"]["ruff"]
        self.assertEqual(ruff["target-version"], "py311")
        self.assertEqual(ruff["lint"]["select"], ["E4", "E7", "E9", "F", "I", "B", "RUF"])

    def test_src_package_root_exists(self):
        package = REPO_ROOT / "src" / "bizman" / "__init__.py"
        self.assertTrue(package.is_file(), "src/bizman package root must exist")

    def test_uv_lock_is_committed_and_matches_project_floor(self):
        lock_path = REPO_ROOT / "uv.lock"
        self.assertTrue(lock_path.is_file(), "uv.lock must be committed")
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], 1)
        self.assertEqual(lock["requires-python"], ">=3.11")
        packages = {item["name"]: item for item in lock["package"]}
        self.assertEqual(packages["jsonschema"]["version"], "4.26.0")
        self.assertEqual(packages["websockets"]["version"], "17.1")
        self.assertEqual(packages["ruff"]["version"], "0.16.3")
        self.assertEqual(packages["import-linter"]["version"], "2.15")
        for name, package in packages.items():
            source = package.get("source", {})
            if name == "bizman":
                self.assertEqual(source, {"editable": "."})
            else:
                self.assertEqual(source.get("registry"), "https://pypi.org/simple")

    def test_built_wheel_and_sdist_are_hygienic_and_assets_stay_external(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "distribution verification requires uv on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            subprocess.run(
                [uv, "build", "--no-sources", "--out-dir", str(dist)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheels = sorted(dist.glob("*.whl"))
            sdists = sorted(dist.glob("*.tar.gz"))
            self.assertEqual(len(wheels), 1)
            self.assertEqual(len(sdists), 1)

            with zipfile.ZipFile(wheels[0]) as archive:
                wheel_names = archive.namelist()
            with tarfile.open(sdists[0], mode="r:gz") as archive:
                sdist_names = archive.getnames()

            for kind, names in (("wheel", wheel_names), ("sdist", sdist_names)):
                forbidden = sorted(name for name in names if _forbidden_distribution_entry(name))
                self.assertEqual(forbidden, [], f"{kind} contains forbidden runtime/dev payloads")

            wheel_relative = {_relative_archive_name(name) for name in wheel_names}
            for external_root in ("config", "schemas", "knowledge"):
                self.assertFalse(
                    any(path.parts and path.parts[0] == external_root for path in wheel_relative),
                    f"wheel must not duplicate repository asset root {external_root!r}",
                )

    def test_wheel_installs_and_cli_runs_outside_repository_checkout(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "distribution verification requires uv on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            subprocess.run(
                [uv, "build", "--no-sources", "--out-dir", str(dist)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(dist.glob("*.whl"))
            venv = root / "venv"
            subprocess.run(
                [uv, "venv", str(venv), "--python", sys.executable],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            console = venv / ("Scripts/bizman.exe" if os.name == "nt" else "bin/bizman")
            subprocess.run(
                [uv, "pip", "install", "--python", str(python), str(wheel)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            env = os.environ.copy()
            for name in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
                env.pop(name, None)
            env["PYTHONNOUSERSITE"] = "1"
            probe = subprocess.run(
                [
                    str(python),
                    "-c",
                    (
                        "import pathlib, bizman, bizman.foundation, bizman.sessions, "
                        "bizman.collector, bizman.changes, bizman.core; "
                        "print(pathlib.Path(bizman.__file__).resolve())"
                    ),
                ],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            installed_path = Path(probe.stdout.strip())
            self.assertNotIn(REPO_ROOT.resolve(), installed_path.parents)

            help_result = subprocess.run(
                [str(console), "--help"],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("collect", help_result.stdout)
            self.assertIn("detect", help_result.stdout)
            self.assertIn("validate", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
