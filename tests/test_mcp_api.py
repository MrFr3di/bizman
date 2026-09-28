from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from bizman.core import (
    CoreContext,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    RepositoryAssets,
    SystemUtcClock,
    get_knowledge,
    resolve_knowledge,
    search_knowledge,
)
from bizman.readmodel import RuntimeProjection, project_curated_knowledge, rebuild_agent_index


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPLETED_AT = "2026-09-28T00:00:00Z"
PRODUCT_REF = "bm.product.carseat"


def _build_context(data_dir: Path) -> CoreContext:
    rebuild_agent_index(
        data_dir / "index" / "agent-index.sqlite3",
        project_curated_knowledge(REPO_ROOT),
        RuntimeProjection(),
        completed_at=COMPLETED_AT,
    )
    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )


def _run(coro):
    return asyncio.run(coro)


class MCPKnowledgeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._tmp.name) / "BizManData"
        cls.context = _build_context(cls.data_dir)

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
            {"evidence.resolve", "evidence.search", "evidence.get"},
        )

        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertIn("inputSchema", tool)
                self.assertIn("outputSchema", tool)
                self.assertEqual(tool["annotations"]["readOnlyHint"], True)
                self.assertEqual(tool["annotations"]["openWorldHint"], False)
                self.assertEqual(tool["annotations"]["idempotentHint"], True)

        search_schema = tools["evidence.search"]["inputSchema"]
        self.assertEqual(search_schema["properties"]["limit"]["default"], 10)
        self.assertEqual(search_schema["properties"]["limit"]["minimum"], 1)
        self.assertEqual(search_schema["properties"]["limit"]["maximum"], 50)

    def test_tools_return_structured_content_equal_to_core_results(self):
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
                resolve_value = await client.call_tool(
                    "evidence.resolve",
                    {"query": PRODUCT_REF},
                )
                search_value = await client.call_tool(
                    "evidence.search",
                    {"query": "Автокресло"},
                )
                get_value = await client.call_tool(
                    "evidence.get",
                    {"ref": PRODUCT_REF},
                )
                return resolve_value, search_value, get_value

        resolved, searched, fetched = _run(scenario())
        for value in (resolved, searched, fetched):
            self.assertFalse(value.is_error)

        self.assertEqual(resolved.structured_content, expected_resolve)
        self.assertEqual(searched.structured_content, expected_search)
        self.assertEqual(fetched.structured_content, expected_get)

        for value in (resolved, searched, fetched):
            encoded = json.dumps(
                value.structured_content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.assertLessEqual(len(encoded), 8 * 1024)

    def test_kind_filter_and_explicit_limit_flow_through_core(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "evidence.search",
                    {
                        "query": "Автокресло",
                        "limit": 1,
                        "kinds": ["product"],
                    },
                )

        result = _run(scenario())
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.structured_content["items"]), 1)
        self.assertEqual(result.structured_content["items"][0]["ref"], PRODUCT_REF)
        self.assertEqual(result.structured_content["items"][0]["kind"], "product")

    def test_expected_core_error_is_sanitized_as_tool_error(self):
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
                    return await client.call_tool(
                        "evidence.resolve",
                        {"query": PRODUCT_REF},
                    )

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

    def test_sdk_schema_rejects_out_of_budget_search_limit(self):
        from mcp import Client
        from bizman.mcp import build_server

        async def scenario():
            async with Client(build_server(self.context)) as client:
                return await client.call_tool(
                    "evidence.search",
                    {"query": "Автокресло", "limit": 51},
                )

        result = _run(scenario())
        self.assertTrue(result.is_error)


class MCPStdioSmokeTests(unittest.TestCase):
    def test_real_stdio_entry_point_lists_and_calls_tool(self):
        from mcp import Client, StdioServerParameters

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _build_context(data_dir)
            server = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "bizman.mcp",
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
                        "evidence.resolve",
                        {"query": PRODUCT_REF},
                    )
                    return listed, result

            listed, result = _run(asyncio.wait_for(scenario(), timeout=30))

        self.assertEqual(
            {tool.name for tool in listed.tools},
            {"evidence.resolve", "evidence.search", "evidence.get"},
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["hit"]["ref"], PRODUCT_REF)


if __name__ == "__main__":
    unittest.main()
