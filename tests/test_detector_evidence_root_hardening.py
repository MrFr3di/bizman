from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.bizman_detector.evidence import EvidenceFormatError, EvidenceReader


SESSION_ID = "01991c7d-a400-7000-8000-000000000091"


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "session_id": SESSION_ID,
        "started_at": "2026-09-07T12:00:00Z",
        "ended_at": None,
        "status": "running",
        "collector": {"name": "bizman-cdp", "version": "root-hardening-test"},
        "browser": {"product": "Chrome", "version": "test"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [],
        "artifact_count": 0,
        "warnings": [],
    }


class EvidenceRootSymlinkHardeningTests(unittest.TestCase):
    @staticmethod
    def _symlink_directory(link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            raise unittest.SkipTest(f"directory symlinks unavailable: {exc}") from exc

    def test_sessions_root_symlink_cannot_escape_data_directory(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "BizManData"
            data_dir.mkdir()
            escaped_sessions = root / "escaped-sessions"
            session_dir = escaped_sessions / SESSION_ID
            session_dir.mkdir(parents=True)
            (session_dir / "manifest.json").write_text(
                json.dumps(_manifest()), encoding="utf-8"
            )
            self._symlink_directory(data_dir / "sessions", escaped_sessions)

            reader = EvidenceReader(repo_root, data_dir)
            with self.assertRaisesRegex(EvidenceFormatError, "sessions root.*data directory"):
                tuple(reader.iter_session_statuses())

    def test_artifact_root_symlink_cannot_escape_data_directory(self):
        repo_root = Path(__file__).resolve().parents[1]
        payload = b"synthetic-safe-artifact"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "BizManData"
            data_dir.mkdir()
            escaped_artifacts = root / "escaped-artifacts"
            artifact_path = escaped_artifacts / "sha256" / digest[:2] / digest
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(payload)
            self._symlink_directory(data_dir / "artifacts", escaped_artifacts)

            reader = EvidenceReader(repo_root, data_dir)
            with self.assertRaisesRegex(EvidenceFormatError, "artifact root.*data directory"):
                reader.read_verified_artifact(f"sha256:{digest}")


if __name__ == "__main__":
    unittest.main()
