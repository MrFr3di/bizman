from __future__ import annotations

from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
import io
import json
import shutil
import tempfile
import unittest

from bizman.cli import index_rebuild as index_rebuild_command
from bizman.core import (
    AgentIndexRebuildRequest,
    AssetError,
    CoreContext,
    RepositoryAssets,
    rebuild_agent_index,
    list_sessions,
    SessionListRequest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_utc(self) -> datetime:
        return self.value


class AgentIndexRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "BizManData"
        self.context = CoreContext(
            assets=RepositoryAssets(REPO_ROOT),
            data_dir=self.data_dir,
            clock=_FixedClock(datetime(2026, 9, 30, 12, 0, tzinfo=UTC)),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rebuild_from_curated_knowledge_with_empty_runtime(self) -> None:
        result = rebuild_agent_index(self.context, AgentIndexRebuildRequest())
        self.assertRegex(result.generation, r"^[0-9a-f]{64}$")
        self.assertEqual(result.schema_version, "2")
        self.assertEqual(result.item_count, 632)
        self.assertEqual(result.session_count, 0)
        self.assertEqual(result.change_count, 0)
        self.assertEqual(result.completed_at, "2026-09-30T12:00:00Z")
        self.assertTrue((self.data_dir / "index" / "agent-index.sqlite3").is_file())

    def test_rebuild_is_semantically_deterministic(self) -> None:
        first = rebuild_agent_index(self.context, AgentIndexRebuildRequest())
        second = rebuild_agent_index(self.context, AgentIndexRebuildRequest())
        self.assertEqual(first.generation, second.generation)
        self.assertEqual(first.item_count, second.item_count)

    def test_rebuilt_index_serves_core_reads(self) -> None:
        rebuild_agent_index(self.context, AgentIndexRebuildRequest())
        page = list_sessions(self.context, SessionListRequest(limit=5))
        self.assertEqual(page.items, ())
        self.assertIsNone(page.next_cursor)

    def test_invalid_repository_fails_as_asset_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "knowledge").mkdir(parents=True)
            shutil.copytree(REPO_ROOT / "config", repo / "config")
            shutil.copytree(REPO_ROOT / "schemas", repo / "schemas")
            bad_context = CoreContext(
                assets=RepositoryAssets(repo),
                data_dir=Path(tmp) / "BizManData",
                clock=_FixedClock(datetime(2026, 9, 30, 12, 0, tzinfo=UTC)),
            )
            with self.assertRaises(AssetError):
                rebuild_agent_index(bad_context, AgentIndexRebuildRequest())

    def test_request_type_is_validated(self) -> None:
        with self.assertRaises(TypeError):
            rebuild_agent_index(self.context, "not-a-request")

    def test_cli_prints_machine_readable_summary(self) -> None:
        from argparse import Namespace

        namespace = Namespace()
        namespace.handler = index_rebuild_command.run
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = index_rebuild_command.run(self.context, namespace)
        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["item_count"], 632)
        self.assertEqual(payload["session_count"], 0)
        self.assertEqual(payload["change_count"], 0)


if __name__ == "__main__":
    unittest.main()
