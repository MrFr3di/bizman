from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import statistics
import sys
import tempfile
from typing import Any

from mcp import Client, StdioServerParameters

from bizman.core import CoreContext, RepositoryAssets, SystemUtcClock
from bizman.mcp import build_server
from bizman.readmodel import (
    EvaluationCase,
    RuntimeProjection,
    evaluation_cases_from_document,
    project_curated_knowledge,
    rebuild_agent_index,
)
from tools.benchmarks.readmodel_core import synthetic_runtime


ROOT = Path(__file__).resolve().parents[2]
COMPLETED_AT = "2026-09-28T00:00:00Z"
PRODUCT_REF = "bm.product.carseat"
EXPECTED_TOOLS = frozenset(
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
    }
)
FORBIDDEN_INPUT_TERMS = (
    "path",
    "sql",
    "database",
    "repo_root",
    "data_dir",
)
COMPACT_BYTES = 8 * 1024
STANDARD_BYTES = 16 * 1024


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _load_cases(root: Path, version: int) -> tuple[EvaluationCase, ...]:
    path = root / "tests" / "fixtures" / f"retrieval_eval_v{version}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return evaluation_cases_from_document(document)


def _ratio(value: int | float, total: int) -> float | None:
    return value / total if total else None


def _metrics(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [item for item in outcomes if item["expected_ref"] is not None]
    negative = [item for item in outcomes if item["expected_ref"] is None]
    top1 = sum(item["rank"] == 1 for item in positive)
    top5 = sum(
        item["rank"] is not None and item["rank"] <= 5
        for item in positive
    )
    reciprocal_rank = sum(
        1.0 / item["rank"]
        for item in positive
        if item["rank"] is not None and item["rank"] <= 5
    )
    evidence_ok = sum(bool(item["evidence_ok"]) for item in positive)
    negative_ok = sum(bool(item["negative_ok"]) for item in negative)
    return {
        "cases": len(outcomes),
        "positive_cases": len(positive),
        "negative_cases": len(negative),
        "recall_at_1": _ratio(top1, len(positive)),
        "recall_at_5": _ratio(top5, len(positive)),
        "mrr": _ratio(reciprocal_rank, len(positive)),
        "evidence_correctness": _ratio(evidence_ok, len(positive)),
        "no_match_accuracy": _ratio(negative_ok, len(negative)),
        "overall_accuracy": (top1 + negative_ok) / len(outcomes),
    }


def _all_metrics_match_baseline(corpora: dict[str, dict[str, Any]]) -> bool:
    for value in corpora.values():
        for key in (
            "recall_at_1",
            "recall_at_5",
            "mrr",
            "evidence_correctness",
            "no_match_accuracy",
        ):
            metric = value[key]
            if metric is not None and metric < 1.0:
                return False
    return True


def _properties(schema: object) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            found.update(str(key) for key in properties)
        for value in schema.values():
            found.update(_properties(value))
    elif isinstance(schema, list):
        for value in schema:
            found.update(_properties(value))
    return found


def _array_contracts(schema: object, *, path: str = "$") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        if schema.get("type") == "array":
            found.append(
                {
                    "path": path,
                    "max_items": schema.get("maxItems"),
                }
            )
        for key, value in schema.items():
            found.extend(_array_contracts(value, path=f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found.extend(_array_contracts(value, path=f"{path}[{index}]"))
    return found


def _structured(result: Any, *, tool: str) -> dict[str, Any]:
    if result.is_error:
        raise AssertionError(f"{tool} returned an MCP tool error")
    value = result.structured_content
    if not isinstance(value, dict):
        raise AssertionError(f"{tool} did not return structured object content")
    return value


async def _call(
    client: Client,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    return _structured(result, tool=tool)


async def _evaluate_retrieval(
    client: Client,
    root: Path,
) -> dict[str, Any]:
    corpora: dict[str, dict[str, Any]] = {}
    for version in (1, 2, 3):
        outcomes: list[dict[str, Any]] = []
        for case in _load_cases(root, version):
            arguments: dict[str, Any] = {
                "query": case.query,
                "limit": 5,
            }
            if case.kinds:
                arguments["kinds"] = [kind.value for kind in case.kinds]
            content = await _call(client, "evidence.search", arguments)
            items = content["items"]
            if not isinstance(items, list):
                raise AssertionError("evidence.search items must be an array")

            if case.expected_ref is None:
                outcomes.append(
                    {
                        "query": case.query,
                        "expected_ref": None,
                        "rank": None,
                        "evidence_ok": False,
                        "negative_ok": not items,
                    }
                )
                continue

            rank: int | None = None
            evidence_ok = False
            for index, item in enumerate(items, 1):
                if item["ref"] == case.expected_ref:
                    rank = index
                    evidence_ok = (
                        case.expected_evidence_ref in item["evidence_refs"]
                    )
                    break
            outcomes.append(
                {
                    "query": case.query,
                    "expected_ref": case.expected_ref,
                    "rank": rank,
                    "evidence_ok": evidence_ok,
                    "negative_ok": False,
                }
            )
        corpora[f"v{version}"] = _metrics(outcomes)

    return {
        "corpora": corpora,
        "baseline": {
            "applicable_metrics_minimum": 1.0,
            "source": "P2-E lexical retrieval baseline",
        },
        "passed": _all_metrics_match_baseline(corpora),
    }


async def _evaluate_action_trace(
    client: Client,
    root: Path,
) -> dict[str, Any]:
    projection = project_curated_knowledge(root)
    actions = [
        record
        for record in projection.records
        if record.kind.value == "action"
    ]
    results: list[dict[str, Any]] = []
    for action in actions:
        calls = 0
        resolved = await _call(
            client,
            "evidence.resolve",
            {"query": action.ref, "kinds": ["action"]},
        )
        calls += 1
        hit = resolved["hit"]
        resolved_ok = (
            isinstance(hit, dict)
            and hit.get("ref") == action.ref
            and bool(hit.get("evidence_refs"))
        )
        traced_ok = False
        evidence_ref: str | None = None
        if resolved_ok:
            evidence_ref = str(hit["evidence_refs"][0])
            traced = await _call(
                client,
                "evidence.trace",
                {"evidence_ref": evidence_ref},
            )
            calls += 1
            trace = traced["trace"]
            traced_ok = (
                isinstance(trace, dict)
                and trace.get("evidence_ref") == evidence_ref
            )
        results.append(
            {
                "ref": action.ref,
                "evidence_ref": evidence_ref,
                "calls": calls,
                "resolved": resolved_ok,
                "traced": traced_ok,
            }
        )

    max_calls = max((item["calls"] for item in results), default=0)
    passed = bool(results) and all(
        item["resolved"] and item["traced"] and item["calls"] <= 2
        for item in results
    )
    return {
        "actions": len(results),
        "max_calls": max_calls,
        "passed": passed,
        "results": results,
    }


async def _evaluate_common_tasks(
    client: Client,
    runtime: RuntimeProjection,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not runtime.sessions or not runtime.changes:
        raise AssertionError("P3 evaluation runtime must contain sessions and changes")

    first_session = runtime.sessions[0]
    second_session = runtime.sessions[1]
    first_change = runtime.changes[0]
    results: dict[str, dict[str, Any]] = {}
    representative: dict[str, dict[str, Any]] = {}

    async def task(
        name: str,
        operations: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for tool, arguments in operations:
            values.append(await _call(client, tool, arguments))
        results[name] = {"calls": len(operations)}
        return values

    values = await task(
        "evidence.resolve",
        [("evidence.resolve", {"query": PRODUCT_REF})],
    )
    representative["evidence.resolve"] = values[-1]

    searched = await task(
        "evidence.search_get",
        [
            ("evidence.search", {"query": "Автокресло"}),
            ("evidence.get", {"ref": PRODUCT_REF}),
        ],
    )
    representative["evidence.search"] = searched[0]
    representative["evidence.get"] = searched[1]

    resolve_trace_calls = 0
    resolved = await _call(
        client,
        "evidence.resolve",
        {"query": PRODUCT_REF},
    )
    resolve_trace_calls += 1
    evidence_ref = resolved["hit"]["evidence_refs"][0]
    trace_value = await _call(
        client,
        "evidence.trace",
        {"evidence_ref": evidence_ref},
    )
    resolve_trace_calls += 1
    results["evidence.resolve_trace"] = {"calls": resolve_trace_calls}
    representative["evidence.trace"] = trace_value

    sessions = await task(
        "sessions.list",
        [("sessions.list", {})],
    )
    representative["sessions.list"] = sessions[-1]

    summary = await task(
        "sessions.summary",
        [
            (
                "sessions.summary",
                {"session_id": first_session.session_id},
            )
        ],
    )
    representative["sessions.summary"] = summary[-1]

    compared = await task(
        "sessions.compare",
        [
            (
                "sessions.compare",
                {
                    "from_session_id": first_session.session_id,
                    "to_session_id": second_session.session_id,
                },
            )
        ],
    )
    representative["sessions.compare"] = compared[-1]

    anomalies = await task(
        "sessions.anomalies",
        [("sessions.anomalies", {})],
    )
    representative["sessions.anomalies"] = anomalies[-1]

    changes = await task(
        "changes.list_get",
        [
            ("changes.list", {}),
            (
                "changes.get",
                {
                    "analysis_profile_sha256": first_change.analysis_profile_sha256,
                    "change_id": first_change.change_id,
                },
            ),
        ],
    )
    representative["changes.list"] = changes[0]
    representative["changes.get"] = changes[1]

    call_counts = [int(item["calls"]) for item in results.values()]
    evidence_session_counts = [
        int(item["calls"])
        for name, item in results.items()
        if name.startswith("evidence.") or name.startswith("sessions.")
    ]
    median_all = statistics.median(call_counts)
    median_evidence_session = statistics.median(evidence_session_counts)
    return (
        {
            "tasks": results,
            "median_calls_all": median_all,
            "median_calls_evidence_session": median_evidence_session,
            "passed": median_evidence_session <= 3,
        },
        representative,
    )


async def _evaluate_surface_and_budgets(
    client: Client,
    runtime: RuntimeProjection,
    representative: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    listed = await client.list_tools()
    tools = {
        tool.name: tool.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        for tool in listed.tools
    }

    tool_names = frozenset(tools)
    schemas_explicit = all(
        "inputSchema" in tool and "outputSchema" in tool
        for tool in tools.values()
    )
    annotations_ok = all(
        tool.get("annotations", {}).get("readOnlyHint") is True
        and tool.get("annotations", {}).get("openWorldHint") is False
        for tool in tools.values()
    )

    forbidden_inputs: dict[str, list[str]] = {}
    output_arrays: dict[str, list[dict[str, Any]]] = {}
    for name, tool in tools.items():
        input_properties = _properties(tool["inputSchema"])
        bad = sorted(
            property_name
            for property_name in input_properties
            if any(
                term in property_name.casefold()
                for term in FORBIDDEN_INPUT_TERMS
            )
        )
        if bad:
            forbidden_inputs[name] = bad
        output_arrays[name] = _array_contracts(tool["outputSchema"])

    arrays_bounded = all(
        isinstance(item["max_items"], int) and item["max_items"] > 0
        for arrays in output_arrays.values()
        for item in arrays
    )

    compact_sizes = {
        name: _json_size(value)
        for name, value in representative.items()
    }

    standard_sessions = await _call(
        client,
        "sessions.list",
        {"limit": 20},
    )
    standard_anomalies = await _call(
        client,
        "sessions.anomalies",
        {"limit": 20},
    )
    standard_changes = await _call(
        client,
        "changes.list",
        {"limit": 20},
    )
    standard_sizes = {
        "sessions.list.20": _json_size(standard_sessions),
        "sessions.anomalies.20": _json_size(standard_anomalies),
        "changes.list.20": _json_size(standard_changes),
    }

    no_silent_truncation = (
        len(standard_sessions["items"]) == min(20, len(runtime.sessions))
        and len(standard_changes["items"]) == min(20, len(runtime.changes))
    )

    compact_max = max(compact_sizes.values())
    standard_max = max((*compact_sizes.values(), *standard_sizes.values()))
    passed = (
        tool_names == EXPECTED_TOOLS
        and schemas_explicit
        and annotations_ok
        and not forbidden_inputs
        and arrays_bounded
        and compact_max <= COMPACT_BYTES
        and standard_max <= STANDARD_BYTES
        and no_silent_truncation
    )
    return {
        "tool_count": len(tools),
        "tools": sorted(tools),
        "schemas_explicit": schemas_explicit,
        "annotations_read_only_closed_world": annotations_ok,
        "forbidden_input_properties": forbidden_inputs,
        "output_arrays": output_arrays,
        "all_output_arrays_bounded": arrays_bounded,
        "compact_result_bytes": compact_sizes,
        "compact_max_bytes": compact_max,
        "compact_target_bytes": COMPACT_BYTES,
        "standard_result_bytes": standard_sizes,
        "standard_max_bytes": standard_max,
        "standard_target_bytes": STANDARD_BYTES,
        "no_silent_truncation": no_silent_truncation,
        "passed": passed,
    }


def _render_error(result: Any) -> str:
    return json.dumps(
        [
            block.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            for block in result.content
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


async def _evaluate_errors(
    client: Client,
    *,
    root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    cases = (
        ("invalid_uuid", "sessions.summary", {"session_id": "not-a-uuid"}),
        ("invalid_limit", "sessions.list", {"limit": 51}),
        ("invalid_cursor", "sessions.list", {"cursor": "not-a-cursor"}),
        (
            "invalid_profile",
            "changes.list",
            {"analysis_profile_sha256": "not-a-profile"},
        ),
        (
            "invalid_evidence_ref",
            "evidence.trace",
            {"evidence_ref": "../capture.har#entry-1"},
        ),
    )
    results: list[dict[str, Any]] = []
    forbidden = (
        "traceback",
        "sqlite",
        str(root).casefold(),
        str(data_dir).casefold(),
    )
    for name, tool, arguments in cases:
        result = await client.call_tool(tool, arguments)
        rendered = _render_error(result)
        clean = result.is_error and not any(
            token and token in rendered.casefold()
            for token in forbidden
        )
        results.append(
            {
                "case": name,
                "is_error": bool(result.is_error),
                "sanitized": clean,
            }
        )

    with tempfile.TemporaryDirectory() as tmp:
        missing_data = Path(tmp) / "BizManData"
        missing_context = CoreContext(
            assets=RepositoryAssets(root),
            data_dir=missing_data,
            clock=SystemUtcClock(),
        )
        async with Client(build_server(missing_context)) as missing_client:
            result = await missing_client.call_tool("sessions.list", {})
        rendered = _render_error(result)
        clean = (
            result.is_error
            and "Agent Index is unavailable" in rendered
            and "traceback" not in rendered.casefold()
            and "sqlite" not in rendered.casefold()
            and str(missing_data).casefold() not in rendered.casefold()
        )
        results.append(
            {
                "case": "missing_agent_index",
                "is_error": bool(result.is_error),
                "sanitized": clean,
            }
        )

    return {
        "cases": results,
        "passed": all(
            item["is_error"] and item["sanitized"]
            for item in results
        ),
    }


async def _stdio_check(
    *,
    root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    console = Path(sys.executable).parent / (
        "bizman-mcp.exe" if sys.platform == "win32" else "bizman-mcp"
    )
    if not console.is_file():
        return {
            "checked": True,
            "passed": False,
            "reason": "installed bizman-mcp entry point is missing",
        }

    server = StdioServerParameters(
        command=str(console),
        args=[
            "--repo-root",
            str(root),
            "--data-dir",
            str(data_dir),
        ],
        cwd=root,
    )

    async def scenario() -> tuple[set[str], dict[str, Any]]:
        async with Client(server) as client:
            listed = await client.list_tools()
            resolved = await _call(
                client,
                "evidence.resolve",
                {"query": PRODUCT_REF},
            )
            return {tool.name for tool in listed.tools}, resolved

    try:
        tool_names, resolved = await asyncio.wait_for(
            scenario(),
            timeout=30,
        )
    except Exception as exc:
        return {
            "checked": True,
            "passed": False,
            "reason": type(exc).__name__,
        }

    hit = resolved.get("hit")
    return {
        "checked": True,
        "passed": (
            tool_names == EXPECTED_TOOLS
            and isinstance(hit, dict)
            and hit.get("ref") == PRODUCT_REF
        ),
        "tool_count": len(tool_names),
    }


async def evaluate_mcp_p3(
    root: Path,
    data_dir: Path,
    *,
    include_stdio: bool = True,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(strict=True)
    data_dir = Path(data_dir).expanduser().resolve(strict=False)
    runtime = synthetic_runtime(sessions=20, changes=40)
    projection = project_curated_knowledge(root)
    rebuild_agent_index(
        data_dir / "index" / "agent-index.sqlite3",
        projection,
        runtime,
        completed_at=COMPLETED_AT,
    )
    context = CoreContext(
        assets=RepositoryAssets(root),
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )

    async with Client(build_server(context)) as client:
        retrieval = await _evaluate_retrieval(client, root)
        action_trace = await _evaluate_action_trace(client, root)
        common_tasks, representative = await _evaluate_common_tasks(
            client,
            runtime,
        )
        surface = await _evaluate_surface_and_budgets(
            client,
            runtime,
            representative,
        )
        errors = await _evaluate_errors(
            client,
            root=root,
            data_dir=data_dir,
        )

    stdio = (
        await _stdio_check(root=root, data_dir=data_dir)
        if include_stdio
        else {"checked": False, "passed": None}
    )

    acceptance = {
        "retrieval_parity": bool(retrieval["passed"]),
        "known_action_trace_max_2_calls": bool(action_trace["passed"]),
        "common_evidence_session_median_calls_max_3": bool(
            common_tasks["passed"]
        ),
        "bounded_explicit_surface_and_results": bool(surface["passed"]),
        "sanitized_errors": bool(errors["passed"]),
        "stdio_protocol_clean": stdio["passed"],
    }
    passed = all(value is True for value in acceptance.values())

    return {
        "evaluation_version": 1,
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "knowledge_records": len(projection.records),
        "runtime_fixture": {
            "sessions": len(runtime.sessions),
            "changes": len(runtime.changes),
        },
        "retrieval": retrieval,
        "action_trace": action_trace,
        "common_tasks": common_tasks,
        "surface": surface,
        "errors": errors,
        "stdio": stdio,
        "acceptance": acceptance,
        "passed": passed,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the fixed BizMan P3 MCP read-only surface."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-stdio",
        action="store_true",
        help="Skip the installed stdio process check; report cannot fully pass.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    temp: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temp = tempfile.TemporaryDirectory(prefix="bizman-p3-eval-")
        data_dir = Path(temp.name) / "BizManData"
    else:
        data_dir = args.data_dir
    try:
        report = asyncio.run(
            evaluate_mcp_p3(
                args.repo_root,
                data_dir,
                include_stdio=not args.skip_stdio,
            )
        )
        payload = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        sys.stdout.write(payload)
        return 0 if report["passed"] else 1
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
