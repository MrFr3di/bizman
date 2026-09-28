from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

from bizman.core import (
    ChangeGetRequest,
    ChangeListRequest,
    CoreContext,
    EvidenceTraceRequest,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    RepositoryAssets,
    SessionAnomalyListRequest,
    SessionCompareRequest,
    SessionGetRequest,
    SessionListRequest,
    SystemUtcClock,
    compare_sessions,
    get_change,
    get_knowledge,
    get_session,
    list_changes,
    list_session_anomalies,
    list_sessions,
    resolve_knowledge,
    search_knowledge,
    trace_evidence,
)
from bizman.readmodel import (
    ChangeIndexRecord,
    RuntimeProjection,
    SessionSummary,
    project_curated_knowledge,
    rebuild_agent_index,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPLETED_AT = "2026-09-28T00:00:00Z"
PRODUCT_REF = "bm.product.carseat"
PROFILE_A = "a" * 64
PROFILE_B = "b" * 64


def _session_id(index: int) -> str:
    return f"01991c7d-a400-7000-8000-{index + 1:012x}"


def _session(index: int) -> SessionSummary:
    minute = index % 60
    return SessionSummary(
        session_id=_session_id(index),
        manifest_sha256=f"{index + 1:064x}",
        evidence_sha256=f"{index + 101:064x}",
        started_at=f"2026-09-28T10:{minute:02d}:00Z",
        ended_at=f"2026-09-28T10:{minute:02d}:30Z",
        status="completed",
        event_count=100 + index,
        action_count=4,
        http_request_count=8,
        http_response_count=8,
        correlation_strong_count=2,
        correlation_probable_count=1,
        correlation_temporal_count=1,
        correlation_exact_count=0,
        uncorrelated_action_count=1 if index % 6 == 0 else 0,
        warning_count=index % 3,
        anomaly_count=index % 2,
    )


def _change(index: int, *, profile: str, session: SessionSummary) -> ChangeIndexRecord:
    return ChangeIndexRecord(
        analysis_profile_sha256=profile,
        change_id=f"chg.{index:04d}",
        rule_id=f"BM-MCP-{index % 5:03d}",
        rule_version=1,
        kind=f"mcp.kind.{index % 4}",
        novelty_class="novel" if index % 2 == 0 else "known",
        first_session_id=session.session_id,
        first_seen_at=session.started_at,
        last_session_id=session.session_id,
        last_seen_at=session.ended_at,
        occurrence_count=1,
    )


def _runtime_projection() -> RuntimeProjection:
    sessions = tuple(_session(index) for index in range(12))
    changes = tuple(
        _change(index, profile=PROFILE_A, session=sessions[index % len(sessions)])
        for index in range(12)
    ) + tuple(
        _change(
            100 + index,
            profile=PROFILE_B,
            session=sessions[(index + 3) % len(sessions)],
        )
        for index in range(3)
    )
    return RuntimeProjection(sessions=sessions, changes=changes)


def _build_context(
    data_dir: Path,
    runtime: RuntimeProjection | None = None,
) -> CoreContext:
    rebuild_agent_index(
        data_dir / "index" / "agent-index.sqlite3",
        project_curated_knowledge(REPO_ROOT),
        runtime if runtime is not None else _runtime_projection(),
        completed_at=COMPLETED_AT,
    )
    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )


def _run(coro):
    return asyncio.run(coro)


def _encoded_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


class MCPProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._tmp.name) / "BizManData"
        cls.runtime = _runtime_projection()
        cls.context = _build_context(cls.data_dir, cls.runtime)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_tool_surface_schemas_and_annotations_are_exact_and_bounded(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.list_tools()

        listed = _run(scenario())
        tools = {
            tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in listed.tools
        }
        self.assertEqual(
            set(tools),
            {
                "evidence.resolve",
                "evidence.search",
                "evidence.get",
                "evidence.trace",
                "sessions.list",
                "sessions.summary",
                "sessions.compare",
                "sessions.anomalies",
                "changes.list",
                "changes.get",
            },
        )

        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertIn("inputSchema", tool)
                self.assertIn("outputSchema", tool)
                self.assertEqual(tool["annotations"]["readOnlyHint"], True)
                self.assertEqual(tool["annotations"]["openWorldHint"], False)
                self.assertEqual(tool["annotations"]["idempotentHint"], True)

        for name in (
            "evidence.search",
            "sessions.list",
            "sessions.anomalies",
            "changes.list",
        ):
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema["properties"]["limit"]["default"], 10)
            self.assertEqual(schema["properties"]["limit"]["minimum"], 1)
            self.assertEqual(schema["properties"]["limit"]["maximum"], 50)

        session_schema = tools["sessions.summary"]["inputSchema"]
        self.assertIn("pattern", session_schema["properties"]["session_id"])
        change_schema = tools["changes.get"]["inputSchema"]
        self.assertIn(
            "pattern",
            change_schema["properties"]["analysis_profile_sha256"],
        )
        trace_schema = tools["evidence.trace"]["inputSchema"]
        self.assertIn("pattern", trace_schema["properties"]["evidence_ref"])
        self.assertEqual(
            trace_schema["properties"]["evidence_ref"]["maxLength"],
            512,
        )

    def test_evidence_tools_remain_equal_to_core_results(self):
        from mcp import Client
        from bizman.mcp import build_server
        from bizman.mcp.models import get_result, resolve_result, search_result

        expected_resolve = resolve_result(
            resolve_knowledge(
                self.context,
                KnowledgeResolveRequest(text=PRODUCT_REF),
            )
        ).model_dump(mode="json")
        expected_search = search_result(
            search_knowledge(
                self.context,
                KnowledgeSearchRequest(text="Автокресло", limit=10),
            )
        ).model_dump(mode="json")
        expected_get = get_result(
            get_knowledge(
                self.context,
                KnowledgeGetRequest(ref=PRODUCT_REF),
            )
        ).model_dump(mode="json")

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return (
                    await client.call_tool(
                        "evidence.resolve",
                        {"query": PRODUCT_REF},
                    ),
                    await client.call_tool(
                        "evidence.search",
                        {"query": "Автокресло"},
                    ),
                    await client.call_tool(
                        "evidence.get",
                        {"ref": PRODUCT_REF},
                    ),
                )

        resolved, searched, fetched = _run(scenario())
        for value in (resolved, searched, fetched):
            self.assertFalse(value.is_error)
            self.assertLessEqual(_encoded_size(value.structured_content), 8 * 1024)

        self.assertEqual(resolved.structured_content, expected_resolve)
        self.assertEqual(searched.structured_content, expected_search)
        self.assertEqual(fetched.structured_content, expected_get)

    def test_evidence_trace_equals_direct_core_result_and_is_compact(self):
        from mcp import Client
        from bizman.mcp import build_server
        from bizman.mcp.models import trace_result

        evidence_ref = "src.har.bizmania.2026-09-06.01#entry-224"
        expected = trace_result(
            trace_evidence(
                self.context,
                EvidenceTraceRequest(evidence_ref=evidence_ref),
            )
        ).model_dump(mode="json")

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "evidence.trace",
                    {"evidence_ref": evidence_ref},
                )

        result = _run(scenario())
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, expected)
        self.assertLessEqual(_encoded_size(result.structured_content), 8 * 1024)
        self.assertFalse(result.structured_content["trace"]["raw_source_committed"])
        self.assertNotIn("Path", json.dumps(result.structured_content))

    def test_evidence_trace_unknown_source_returns_null(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "evidence.trace",
                    {"evidence_ref": "unknown.source#entry-1"},
                )

        result = _run(scenario())
        self.assertFalse(result.is_error)
        self.assertIsNone(result.structured_content["trace"])

    def test_session_and_change_tools_equal_direct_core_results(self):
        from mcp import Client
        from bizman.mcp import build_server
        from bizman.mcp.models import (
            change_get_result,
            change_list_result,
            session_list_result,
            session_summary_result,
        )

        first_session = self.runtime.sessions[0]
        first_change = next(
            item
            for item in self.runtime.changes
            if item.analysis_profile_sha256 == PROFILE_A
        )
        expected_sessions = session_list_result(
            list_sessions(self.context, SessionListRequest(limit=10))
        ).model_dump(mode="json")
        expected_session = session_summary_result(
            get_session(
                self.context,
                SessionGetRequest(session_id=first_session.session_id),
            )
        ).model_dump(mode="json")
        expected_changes = change_list_result(
            list_changes(
                self.context,
                ChangeListRequest(
                    limit=10,
                    analysis_profile_sha256=PROFILE_A,
                ),
            )
        ).model_dump(mode="json")
        expected_change = change_get_result(
            get_change(
                self.context,
                ChangeGetRequest(
                    analysis_profile_sha256=PROFILE_A,
                    change_id=first_change.change_id,
                ),
            )
        ).model_dump(mode="json")

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return (
                    await client.call_tool("sessions.list", {}),
                    await client.call_tool(
                        "sessions.summary",
                        {"session_id": first_session.session_id},
                    ),
                    await client.call_tool(
                        "changes.list",
                        {"analysis_profile_sha256": PROFILE_A},
                    ),
                    await client.call_tool(
                        "changes.get",
                        {
                            "analysis_profile_sha256": PROFILE_A,
                            "change_id": first_change.change_id,
                        },
                    ),
                )

        sessions, session, changes, change = _run(scenario())
        for value in (sessions, session, changes, change):
            self.assertFalse(value.is_error)

        self.assertEqual(sessions.structured_content, expected_sessions)
        self.assertEqual(session.structured_content, expected_session)
        self.assertEqual(changes.structured_content, expected_changes)
        self.assertEqual(change.structured_content, expected_change)
        self.assertEqual(len(sessions.structured_content["items"]), 10)
        self.assertEqual(len(changes.structured_content["items"]), 10)
        self.assertLessEqual(_encoded_size(sessions.structured_content), 8 * 1024)
        self.assertLessEqual(_encoded_size(changes.structured_content), 8 * 1024)

    def test_session_intelligence_tools_equal_core_and_keep_signed_deltas(self):
        from mcp import Client
        from bizman.mcp import build_server
        from bizman.mcp.models import (
            session_anomaly_list_result,
            session_compare_result,
        )

        before = self.runtime.sessions[0]
        after = self.runtime.sessions[1]
        expected_compare = session_compare_result(
            compare_sessions(
                self.context,
                SessionCompareRequest(
                    from_session_id=before.session_id,
                    to_session_id=after.session_id,
                ),
            )
        ).model_dump(mode="json")
        expected_anomalies = session_anomaly_list_result(
            list_session_anomalies(
                self.context,
                SessionAnomalyListRequest(limit=10),
            )
        ).model_dump(mode="json")

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return (
                    await client.call_tool(
                        "sessions.compare",
                        {
                            "from_session_id": before.session_id,
                            "to_session_id": after.session_id,
                        },
                    ),
                    await client.call_tool("sessions.anomalies", {}),
                )

        compared, anomalies = _run(scenario())
        self.assertFalse(compared.is_error)
        self.assertFalse(anomalies.is_error)
        self.assertEqual(compared.structured_content, expected_compare)
        self.assertEqual(anomalies.structured_content, expected_anomalies)
        self.assertEqual(
            compared.structured_content["comparison"]["event_count_delta"],
            1,
        )
        self.assertEqual(
            compared.structured_content["comparison"]["warning_count_delta"],
            1,
        )
        self.assertEqual(
            compared.structured_content["comparison"][
                "uncorrelated_action_count_delta"
            ],
            -1,
        )
        self.assertEqual(len(anomalies.structured_content["items"]), 10)
        self.assertLessEqual(_encoded_size(compared.structured_content), 8 * 1024)
        self.assertLessEqual(_encoded_size(anomalies.structured_content), 8 * 1024)

    def test_session_compare_missing_side_is_explicit(self):
        from mcp import Client
        from bizman.mcp import build_server

        missing_id = _session_id(99)

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "sessions.compare",
                    {
                        "from_session_id": self.runtime.sessions[0].session_id,
                        "to_session_id": missing_id,
                    },
                )

        result = _run(scenario())
        self.assertFalse(result.is_error)
        self.assertIsNone(result.structured_content["comparison"])
        self.assertEqual(
            result.structured_content["missing_session_ids"],
            [missing_id],
        )

    def test_session_anomaly_pagination_has_no_duplicates_or_gaps(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            collected: list[str] = []
            cursor = None
            async with Client(build_server(self.context)) as client:
                while True:
                    arguments = {"limit": 5}
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    page = await client.call_tool(
                        "sessions.anomalies",
                        arguments,
                    )
                    self.assertFalse(page.is_error)
                    collected.extend(
                        item["session_id"]
                        for item in page.structured_content["items"]
                    )
                    cursor = page.structured_content.get("next_cursor")
                    if cursor is None:
                        return collected

        collected = _run(scenario())
        expected = [
            item.session_id
            for item in list_session_anomalies(
                self.context,
                SessionAnomalyListRequest(limit=50),
            ).items
        ]
        self.assertEqual(collected, expected)
        self.assertEqual(len(collected), 12)
        self.assertEqual(len(collected), len(set(collected)))

    def test_session_pagination_has_no_duplicates_or_gaps(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            collected: list[str] = []
            cursor = None
            async with Client(build_server(self.context)) as client:
                while True:
                    arguments = {"limit": 5}
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    page = await client.call_tool("sessions.list", arguments)
                    self.assertFalse(page.is_error)
                    collected.extend(
                        item["session_id"]
                        for item in page.structured_content["items"]
                    )
                    cursor = page.structured_content.get("next_cursor")
                    if cursor is None:
                        return collected

        collected = _run(scenario())
        expected = [
            item.session_id
            for item in list_sessions(
                self.context,
                SessionListRequest(limit=50),
            ).items
        ]
        self.assertEqual(collected, expected)
        self.assertEqual(len(collected), len(set(collected)))

    def test_change_pagination_preserves_profile_scope(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            collected: list[tuple[str, str]] = []
            cursor = None
            async with Client(build_server(self.context)) as client:
                while True:
                    arguments = {
                        "limit": 5,
                        "analysis_profile_sha256": PROFILE_A,
                    }
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    page = await client.call_tool("changes.list", arguments)
                    self.assertFalse(page.is_error)
                    collected.extend(
                        (
                            item["analysis_profile_sha256"],
                            item["change_id"],
                        )
                        for item in page.structured_content["items"]
                    )
                    cursor = page.structured_content.get("next_cursor")
                    if cursor is None:
                        return collected

        collected = _run(scenario())
        expected = [
            (item.analysis_profile_sha256, item.change_id)
            for item in list_changes(
                self.context,
                ChangeListRequest(
                    limit=50,
                    analysis_profile_sha256=PROFILE_A,
                ),
            ).items
        ]
        self.assertEqual(collected, expected)
        self.assertEqual({profile for profile, _ in collected}, {PROFILE_A})
        self.assertEqual(len(collected), len(set(collected)))

    def test_cursor_from_changed_generation_fails_as_sanitized_tool_error(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def first_page():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool("sessions.list", {"limit": 5})

        page = _run(first_page())
        self.assertFalse(page.is_error)
        cursor = page.structured_content["next_cursor"]
        self.assertIsNotNone(cursor)

        changed = RuntimeProjection(
            sessions=(
                replace(self.runtime.sessions[0], event_count=999),
                *self.runtime.sessions[1:],
            ),
            changes=self.runtime.changes,
        )
        _build_context(self.data_dir, changed)

        async def stale_cursor():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "sessions.list",
                    {"limit": 5, "cursor": cursor},
                )

        result = _run(stale_cursor())
        self.assertTrue(result.is_error)
        rendered = json.dumps(
            [
                block.model_dump(mode="json", by_alias=True, exclude_none=True)
                for block in result.content
            ],
            ensure_ascii=False,
        )
        self.assertIn("Invalid BizMan read request", rendered)
        self.assertNotIn("generation", rendered.casefold())
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn(str(self.data_dir), rendered)

        _build_context(self.data_dir, self.runtime)

    def test_session_anomaly_cursor_is_scoped_and_generation_bound(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def first_pages():
            async with Client(build_server(self.context)) as client:
                ordinary = await client.call_tool(
                    "sessions.list",
                    {"limit": 5},
                )
                anomalies = await client.call_tool(
                    "sessions.anomalies",
                    {"limit": 5},
                )
                return ordinary, anomalies

        ordinary, anomalies = _run(first_pages())
        self.assertFalse(ordinary.is_error)
        self.assertFalse(anomalies.is_error)
        ordinary_cursor = ordinary.structured_content["next_cursor"]
        anomaly_cursor = anomalies.structured_content["next_cursor"]
        self.assertIsNotNone(ordinary_cursor)
        self.assertIsNotNone(anomaly_cursor)

        async def wrong_scope():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "sessions.anomalies",
                    {"limit": 5, "cursor": ordinary_cursor},
                )

        wrong = _run(wrong_scope())
        self.assertTrue(wrong.is_error)

        changed = RuntimeProjection(
            sessions=(
                replace(self.runtime.sessions[0], event_count=999),
                *self.runtime.sessions[1:],
            ),
            changes=self.runtime.changes,
        )
        _build_context(self.data_dir, changed)

        async def stale_generation():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "sessions.anomalies",
                    {"limit": 5, "cursor": anomaly_cursor},
                )

        stale = _run(stale_generation())
        self.assertTrue(stale.is_error)
        rendered = json.dumps(
            [
                block.model_dump(mode="json", by_alias=True, exclude_none=True)
                for block in stale.content
            ],
            ensure_ascii=False,
        )
        self.assertIn("Invalid BizMan read request", rendered)
        self.assertNotIn("generation", rendered.casefold())
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn(str(self.data_dir), rendered)

        _build_context(self.data_dir, self.runtime)

    def test_invalid_inputs_fail_without_internal_details(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return (
                    await client.call_tool(
                        "sessions.list",
                        {"limit": 51},
                    ),
                    await client.call_tool(
                        "sessions.list",
                        {"cursor": "not-a-core-cursor"},
                    ),
                    await client.call_tool(
                        "sessions.summary",
                        {"session_id": "not-a-uuid"},
                    ),
                    await client.call_tool(
                        "sessions.compare",
                        {
                            "from_session_id": "not-a-uuid",
                            "to_session_id": self.runtime.sessions[0].session_id,
                        },
                    ),
                    await client.call_tool(
                        "sessions.anomalies",
                        {"limit": 51},
                    ),
                    await client.call_tool(
                        "changes.list",
                        {"analysis_profile_sha256": "not-a-profile"},
                    ),
                    await client.call_tool(
                        "evidence.trace",
                        {"evidence_ref": "../raw.har#entry-1"},
                    ),
                )

        results = _run(scenario())
        self.assertTrue(all(item.is_error for item in results))
        rendered = json.dumps(
            [
                [
                    block.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for block in item.content
                ]
                for item in results
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn("sqlite", rendered.casefold())
        self.assertNotIn(str(self.data_dir), rendered)

    def test_missing_index_error_remains_sanitized(self):
        from mcp import Client
        from bizman.mcp import build_server

        with tempfile.TemporaryDirectory() as tmp:
            missing_data = Path(tmp) / "BizManData"
            context = CoreContext(
                assets=RepositoryAssets(REPO_ROOT),
                data_dir=missing_data,
                clock=SystemUtcClock(),
            )

            async def scenario():
                async with Client(build_server(context)) as client:
                    return await client.call_tool("sessions.list", {})

            result = _run(scenario())

        self.assertTrue(result.is_error)
        rendered = json.dumps(
            [
                block.model_dump(mode="json", by_alias=True, exclude_none=True)
                for block in result.content
            ],
            ensure_ascii=False,
        )
        self.assertIn("Agent Index is unavailable", rendered)
        self.assertNotIn(str(missing_data), rendered)
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn("sqlite", rendered.casefold())


class MCPStdioSmokeTests(unittest.TestCase):
    def test_real_stdio_entry_point_lists_and_calls_new_tool(self):
        from mcp import Client, StdioServerParameters

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_context(data_dir)
            console = Path(sys.executable).parent / (
                "bizman-mcp.exe" if sys.platform == "win32" else "bizman-mcp"
            )
            self.assertTrue(console.is_file(), "installed bizman-mcp entry point is required")
            server = StdioServerParameters(
                command=str(console),
                args=[
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                ],
                cwd=REPO_ROOT,
            )

            async def scenario():
                async with Client(server) as client:
                    listed = await client.list_tools()
                    result = await client.call_tool(
                        "sessions.anomalies",
                        {},
                    )
                    return listed, result

            listed, result = _run(asyncio.wait_for(scenario(), timeout=30))

        self.assertEqual(
            {tool.name for tool in listed.tools},
            {
                "evidence.resolve",
                "evidence.search",
                "evidence.get",
                "evidence.trace",
                "sessions.list",
                "sessions.summary",
                "sessions.compare",
                "sessions.anomalies",
                "changes.list",
                "changes.get",
            },
        )
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.structured_content["items"]), 10)
        self.assertLessEqual(_encoded_size(result.structured_content), 8 * 1024)


if __name__ == "__main__":
    unittest.main()
