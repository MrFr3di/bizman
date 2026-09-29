from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.changes.profile import compile_default_analysis_profile
from bizman.core import (
    ContractMismatchError,
    CoreContext,
    CurrentStateRebuildRequest,
    CurrentStateRebuildResult,
    RepositoryAssets,
    rebuild_current_state,
)
from bizman.current import APPLICATION_ID, CurrentStateStore
from bizman.foundation.redaction import load_redaction_policy


REPO_ROOT = Path(__file__).resolve().parents[1]


class FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


def _context(data_dir: Path) -> CoreContext:
    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=FixedClock(),
    )


class CoreCurrentStateTests(unittest.TestCase):
    def test_request_and_result_are_frozen_slotted_and_path_free(self):
        request = CurrentStateRebuildRequest()
        result = CurrentStateRebuildResult(
            projection_name="bizman.current",
            projection_version=1,
            analysis_profile_sha256="a" * 64,
            input_fingerprint="b" * 64,
            state_fingerprint="c" * 64,
            status="ready",
            stale_reason=None,
            session_count=0,
            last_session_id=None,
            last_sequence=None,
        )

        for value in (request, result):
            self.assertFalse(hasattr(value, "__dict__"))
            for field in fields(type(value)):
                self.assertNotIn("Path", str(field.type))
            with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                setattr(value, "_probe", True)

        self.assertEqual(tuple(fields(CurrentStateRebuildRequest)), ())

    def test_rebuild_uses_fixed_internal_path_and_canonical_detector_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            context = _context(data_dir)
            result = rebuild_current_state(
                context,
                CurrentStateRebuildRequest(),
            )

            expected_profile = compile_default_analysis_profile(
                REPO_ROOT,
                load_redaction_policy(
                    REPO_ROOT / "config" / "redaction-policy.json"
                ),
            ).profile
            state_path = data_dir / "state" / "current.sqlite3"
            self.assertTrue(state_path.is_file())
            with CurrentStateStore.open_read_only_if_exists(state_path) as store:
                snapshot = store.snapshot()

        self.assertIsInstance(result, CurrentStateRebuildResult)
        self.assertEqual(
            result.analysis_profile_sha256,
            expected_profile.sha256,
        )
        self.assertEqual(result.session_count, 0)
        self.assertEqual(result.status, "ready")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            snapshot.metadata.state_fingerprint,
            result.state_fingerprint,
        )

    def test_foreign_current_state_database_maps_to_contract_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            state_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(state_path)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID + 1}")
            connection.close()

            with self.assertRaises(ContractMismatchError):
                rebuild_current_state(
                    _context(data_dir),
                    CurrentStateRebuildRequest(),
                )


if __name__ == "__main__":
    unittest.main()
