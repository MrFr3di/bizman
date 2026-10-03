from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time
import tracemalloc
from typing import Callable

from bizman.core import (
    ChangeGetRequest,
    ChangeListRequest,
    CoreContext,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    RepositoryAssets,
    SessionGetRequest,
    SessionListRequest,
    SystemUtcClock,
    get_change,
    get_knowledge,
    get_session,
    list_changes,
    list_sessions,
    resolve_knowledge,
    search_knowledge,
)
from bizman.readmodel import (
    ChangeIndexRecord,
    KnowledgeIndex,
    RuntimeProjection,
    SearchQuery,
    SessionSummary,
    evaluate_retrieval,
    evaluation_cases_from_document,
    project_curated_knowledge,
    rebuild_agent_index,
)


ROOT = Path(__file__).resolve().parents[2]
COMPLETED_AT = "2026-09-28T00:00:00Z"
EXACT_REF = "bm.product.carseat"
EXACT_ALIAS = "product:416"
FTS_QUERY = "автозакупка недельного потребления"
WIKI_REF = "bm.wiki.v1.b3142a5c857c9885c7d64247"


def _positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _session_id(index: int) -> str:
    if index < 0:
        raise ValueError("session index must be non-negative")
    return f"01991c7d-a400-7000-8000-{index + 1:012x}"


def synthetic_runtime(
    *,
    sessions: int,
    changes: int,
) -> RuntimeProjection:
    session_count = _positive_int(sessions, name="sessions")
    if isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
        raise ValueError("changes must be a non-negative integer")

    base = datetime(2026, 9, 28, tzinfo=UTC)
    session_values: list[SessionSummary] = []
    for index in range(session_count):
        started = base + timedelta(seconds=index * 2)
        ended = started + timedelta(seconds=1)
        session_values.append(
            SessionSummary(
                session_id=_session_id(index),
                manifest_sha256=_digest(f"manifest-{index}"),
                evidence_sha256=_digest(f"evidence-{index}"),
                started_at=started.isoformat().replace("+00:00", "Z"),
                ended_at=ended.isoformat().replace("+00:00", "Z"),
                status="completed",
                event_count=100 + index,
                action_count=4,
                http_request_count=8,
                http_response_count=8,
                correlation_strong_count=2,
                correlation_probable_count=1,
                correlation_temporal_count=1,
                correlation_exact_count=0,
                uncorrelated_action_count=0,
                warning_count=index % 3,
                anomaly_count=index % 2,
            )
        )

    profiles = (_digest("profile-a"), _digest("profile-b"))
    change_values: list[ChangeIndexRecord] = []
    for index in range(changes):
        session = session_values[index % session_count]
        change_values.append(
            ChangeIndexRecord(
                analysis_profile_sha256=profiles[index % len(profiles)],
                change_id=f"chg.{_digest(f'change-{index}')}",
                rule_id=f"BM-EVAL-{index % 7:03d}",
                rule_version=1,
                kind=f"synthetic.kind.{index % 5}",
                novelty_class="novel" if index % 2 == 0 else "known",
                first_session_id=session.session_id,
                first_seen_at=session.started_at,
                last_session_id=session.session_id,
                last_seen_at=session.ended_at,
                occurrence_count=1,
            )
        )
    return RuntimeProjection(
        sessions=tuple(session_values),
        changes=tuple(change_values),
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return ordered[position]


def _timings(
    operation: Callable[[], object],
    *,
    repeats: int,
    warmup: bool,
) -> dict[str, float | int]:
    repeat_count = _positive_int(repeats, name="repeats")
    if warmup:
        operation()
    values: list[float] = []
    for _ in range(repeat_count):
        started = time.perf_counter_ns()
        operation()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    p50 = statistics.median(values)
    return {
        "samples": repeat_count,
        "p50_ms": p50,
        "p95_ms": _percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
        "ops_per_second_at_p50": 1000.0 / p50 if p50 > 0 else 0.0,
    }


def _serialized_bytes(value: object) -> int:
    return len(
        json.dumps(
            asdict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _evaluation_report(index: KnowledgeIndex, repo_root: Path) -> dict[str, object]:
    reports: dict[str, object] = {}
    for version in (1, 2, 3):
        path = repo_root / "tests" / "fixtures" / f"retrieval_eval_v{version}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        cases = evaluation_cases_from_document(document)
        reports[f"v{version}"] = asdict(evaluate_retrieval(index, cases))

    v3 = reports["v3"]
    assert isinstance(v3, dict)
    no_gap = (
        v3.get("recall_at_1") == 1.0
        and v3.get("recall_at_5") == 1.0
        and v3.get("evidence_correctness") == 1.0
        and v3.get("no_match_accuracy") == 1.0
    )
    return {
        "corpora": reports,
        "lexical_gap_detected": not no_gap,
        "decision": (
            "keep_lexical_baseline"
            if no_gap
            else "review_failed_eval_cases_before_retrieval_experiment"
        ),
    }


def _open_validation(path: Path, *, repeats: int) -> dict[str, float | int]:
    return _timings(
        lambda: KnowledgeIndex(path).close(),
        repeats=repeats,
        warmup=True,
    )


def _core_context(repo_root: Path, data_dir: Path) -> CoreContext:
    return CoreContext(
        assets=RepositoryAssets(repo_root),
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )


def _cold_core_report(
    repo_root: Path,
    data_dir: Path,
    runtime: RuntimeProjection,
    *,
    repeats: int,
) -> tuple[dict[str, object], dict[str, int]]:
    context = _core_context(repo_root, data_dir)
    first_session = runtime.sessions[0]
    first_change = runtime.changes[0]

    operations: dict[str, Callable[[], object]] = {
        "resolve_exact_ref": lambda: resolve_knowledge(
            context,
            KnowledgeResolveRequest(text=EXACT_REF),
        ),
        "resolve_exact_alias": lambda: resolve_knowledge(
            context,
            KnowledgeResolveRequest(text=EXACT_ALIAS),
        ),
        "search_fts": lambda: search_knowledge(
            context,
            KnowledgeSearchRequest(text=FTS_QUERY, limit=5),
        ),
        "knowledge_get": lambda: get_knowledge(
            context,
            KnowledgeGetRequest(ref=WIKI_REF),
        ),
        "sessions_page_20": lambda: list_sessions(
            context,
            SessionListRequest(limit=20),
        ),
        "session_get": lambda: get_session(
            context,
            SessionGetRequest(session_id=first_session.session_id),
        ),
        "changes_page_20": lambda: list_changes(
            context,
            ChangeListRequest(limit=20),
        ),
        "changes_profile_page_20": lambda: list_changes(
            context,
            ChangeListRequest(
                limit=20,
                analysis_profile_sha256=first_change.analysis_profile_sha256,
            ),
        ),
        "change_get": lambda: get_change(
            context,
            ChangeGetRequest(
                analysis_profile_sha256=first_change.analysis_profile_sha256,
                change_id=first_change.change_id,
            ),
        ),
    }
    timings = {
        name: _timings(operation, repeats=repeats, warmup=True)
        for name, operation in operations.items()
    }
    sizes = {
        name: _serialized_bytes(operation())
        for name, operation in operations.items()
    }
    return timings, sizes


def _warm_index_report(
    index: KnowledgeIndex,
    runtime: RuntimeProjection,
    *,
    repeats: int,
) -> dict[str, object]:
    first_session = runtime.sessions[0]
    first_change = runtime.changes[0]
    operations: dict[str, Callable[[], object]] = {
        "resolve_exact_ref": lambda: index.search(SearchQuery(EXACT_REF, limit=1)),
        "resolve_exact_alias": lambda: index.search(SearchQuery(EXACT_ALIAS, limit=1)),
        "search_fts": lambda: index.search(SearchQuery(FTS_QUERY, limit=5)),
        "knowledge_get": lambda: index.knowledge_record(WIKI_REF),
        "sessions_page_20": lambda: index.session_page(limit=20),
        "session_get": lambda: index.session_record(first_session.session_id),
        "changes_page_20": lambda: index.change_page(limit=20),
        "changes_profile_page_20": lambda: index.change_page(
            limit=20,
            analysis_profile_sha256=first_change.analysis_profile_sha256,
        ),
        "change_get": lambda: index.change_record(
            first_change.analysis_profile_sha256,
            first_change.change_id,
        ),
    }
    return {
        name: _timings(operation, repeats=repeats, warmup=True)
        for name, operation in operations.items()
    }


def _query_plans(
    path: Path,
    runtime: RuntimeProjection,
) -> dict[str, list[str]]:
    first_session = runtime.sessions[0]
    first_change = runtime.changes[0]
    queries: dict[str, tuple[str, tuple[object, ...]]] = {
        "knowledge_exact_ref": (
            """
            SELECT r.ref, r.kind, i.title
            FROM ref AS r
            JOIN knowledge_item AS i ON i.ref = r.ref
            WHERE r.ref = ?
            ORDER BY r.ref
            """,
            (EXACT_REF,),
        ),
        "session_exact": (
            "SELECT * FROM session_summary WHERE session_id = ?",
            (first_session.session_id,),
        ),
        "session_page": (
            """
            SELECT *
            FROM session_summary
            WHERE started_at > ? OR (started_at = ? AND session_id > ?)
            ORDER BY started_at, session_id
            LIMIT ?
            """,
            (
                first_session.started_at,
                first_session.started_at,
                first_session.session_id,
                21,
            ),
        ),
        "change_exact": (
            """
            SELECT *
            FROM change_index
            WHERE analysis_profile_sha256 = ? AND change_id = ?
            """,
            (first_change.analysis_profile_sha256, first_change.change_id),
        ),
        "change_page_unfiltered": (
            """
            SELECT *
            FROM change_index
            ORDER BY analysis_profile_sha256, change_id
            LIMIT ?
            """,
            (21,),
        ),
        "change_page_filtered": (
            """
            SELECT *
            FROM change_index
            WHERE analysis_profile_sha256 = ?
            ORDER BY analysis_profile_sha256, change_id
            LIMIT ?
            """,
            (first_change.analysis_profile_sha256, 21),
        ),
        "fts": (
            """
            SELECT r.ref, r.kind, i.title, bm25(knowledge_fts) AS score
            FROM knowledge_fts
            JOIN ref AS r ON r.ref = knowledge_fts.ref
            JOIN knowledge_item AS i ON i.ref = r.ref
            WHERE knowledge_fts MATCH ?
            ORDER BY score, r.ref
            LIMIT ?
            """,
            ('"автозакупка" "недельного" "потребления"', 5),
        ),
    }
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        return {
            name: [
                str(row[3])
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN " + sql,
                    parameters,
                )
            ]
            for name, (sql, parameters) in queries.items()
        }
    finally:
        connection.close()


def _rebuild_report(
    root: Path,
    projection: object,
    *,
    label: str,
    runtime: RuntimeProjection,
) -> dict[str, object]:
    path = root / f"{label}.sqlite3"
    tracemalloc.start()
    started = time.perf_counter()
    try:
        generation = rebuild_agent_index(
            path,
            projection,
            runtime,
            completed_at=COMPLETED_AT,
        )
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    with KnowledgeIndex(path) as index:
        metadata = index.metadata()
    return {
        "sessions": len(runtime.sessions),
        "changes": len(runtime.changes),
        "elapsed_seconds": elapsed,
        "database_bytes": path.stat().st_size,
        "peak_tracemalloc_bytes": peak,
        "generation": generation,
        "item_count": int(metadata["item_count"]),
        "session_count": int(metadata["session_count"]),
        "change_count": int(metadata["change_count"]),
    }


def benchmark_readmodel_core(
    repo_root: Path,
    *,
    repeats: int = 7,
    small_sessions: int = 20,
    small_changes: int = 40,
    large_sessions: int = 200,
    large_changes: int = 400,
    work_root: Path | None = None,
) -> dict[str, object]:
    repeat_count = _positive_int(repeats, name="repeats")
    root = Path(repo_root).expanduser().resolve(strict=True)
    projection = project_curated_knowledge(root)
    small_runtime = synthetic_runtime(
        sessions=small_sessions,
        changes=small_changes,
    )
    large_runtime = synthetic_runtime(
        sessions=large_sessions,
        changes=large_changes,
    )

    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if work_root is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="bizman-readmodel-benchmark-")
        base = Path(owned_temp.name)
    else:
        base = Path(work_root).expanduser().resolve(strict=False)
        base.mkdir(parents=True, exist_ok=True)

    try:
        data_dir = base / "live" / "BizManData"
        path = data_dir / "index" / "agent-index.sqlite3"
        rebuild_agent_index(
            path,
            projection,
            small_runtime,
            completed_at=COMPLETED_AT,
        )

        with KnowledgeIndex(path) as index:
            evaluation = _evaluation_report(index, root)
            warm = _warm_index_report(
                index,
                small_runtime,
                repeats=repeat_count,
            )

        cold, result_sizes = _cold_core_report(
            root,
            data_dir,
            small_runtime,
            repeats=repeat_count,
        )
        integrity_open = _open_validation(path, repeats=repeat_count)
        plans = _query_plans(path, small_runtime)

        rebuild_root = base / "rebuild"
        rebuild_root.mkdir(parents=True, exist_ok=True)
        rebuilds = {
            "curated_only": _rebuild_report(
                rebuild_root,
                projection,
                label="curated-only",
                runtime=RuntimeProjection(),
            ),
            "small_runtime": _rebuild_report(
                rebuild_root,
                projection,
                label="small-runtime",
                runtime=small_runtime,
            ),
            "large_runtime": _rebuild_report(
                rebuild_root,
                projection,
                label="large-runtime",
                runtime=large_runtime,
            ),
        }

        return {
            "benchmark_version": 1,
            "semantics": {
                "cold_core": (
                    "fresh KnowledgeIndex open+integrity validation per Core call; "
                    "OS page cache is not flushed"
                ),
                "warm_index": "one validated KnowledgeIndex handle reused across calls",
                "timing_policy": "non-gating on shared CI runners",
            },
            "corpus": {
                "knowledge_items": len(projection.records),
                "small_runtime_sessions": len(small_runtime.sessions),
                "small_runtime_changes": len(small_runtime.changes),
                "large_runtime_sessions": len(large_runtime.sessions),
                "large_runtime_changes": len(large_runtime.changes),
            },
            "evaluation": evaluation,
            "latency": {
                "cold_core": cold,
                "warm_index": warm,
                "integrity_open": integrity_open,
            },
            "serialized_result_bytes": result_sizes,
            "rebuild": rebuilds,
            "query_plans": plans,
        }
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-gating Agent Index/Core evaluation and performance benchmark."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--small-sessions", type=int, default=20)
    parser.add_argument("--small-changes", type=int, default=40)
    parser.add_argument("--large-sessions", type=int, default=200)
    parser.add_argument("--large-changes", type=int, default=400)
    args = parser.parse_args(argv)

    result = benchmark_readmodel_core(
        args.repo_root,
        repeats=args.repeats,
        small_sessions=args.small_sessions,
        small_changes=args.small_changes,
        large_sessions=args.large_sessions,
        large_changes=args.large_changes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
