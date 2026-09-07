#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bizman_detector.baseline import BaselineCompiler
from tools.bizman_detector.evidence import EvidenceReader
from tools.bizman_detector.extract import ObservationExtractor
from tools.bizman_detector.model import PathMatch
from tools.bizman_detector.normalization import (
    AmbiguousPathError,
    PathMatcher,
    normalize_origin_relative_path,
)
from tools.bizman_foundation.redaction import RedactionPolicy, load_redaction_policy


SESSION_ID = "01991c7d-a400-7000-8000-00000000b001"
POST_PATH = "/units/customise/headquarterretailsettings/"
GET_PATH = "/analitics/vendors/"
_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_TRIE_CONSIDER_THRESHOLD = 1.25
_VALIDATION_PASSES = 3


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _segments(path: str) -> tuple[str, ...]:
    if path == "/":
        return ()
    return tuple(path[1:].split("/"))


def _is_placeholder(segment: str) -> bool:
    return _PLACEHOLDER_RE.fullmatch(segment) is not None


@dataclass(frozen=True, slots=True)
class _Template:
    pattern: str
    segments: tuple[str, ...]
    literal_count: int
    placeholder_count: int

    @property
    def specificity(self) -> tuple[int, int]:
        return (self.literal_count, -self.placeholder_count)


def _compile_patterns(patterns: Iterable[str]) -> tuple[frozenset[str], tuple[_Template, ...]]:
    exact: set[str] = set()
    templates: list[_Template] = []
    for raw_pattern in patterns:
        pattern = normalize_origin_relative_path(raw_pattern)
        segments = _segments(pattern)
        placeholder_count = sum(_is_placeholder(segment) for segment in segments)
        if placeholder_count == 0:
            exact.add(pattern)
            continue
        for segment in segments:
            if (segment.startswith("{") or segment.endswith("}")) and not _is_placeholder(segment):
                raise ValueError(f"invalid path placeholder in {pattern!r}")
        templates.append(
            _Template(
                pattern=pattern,
                segments=segments,
                literal_count=len(segments) - placeholder_count,
                placeholder_count=placeholder_count,
            )
        )
    return frozenset(exact), tuple(sorted(templates, key=lambda item: item.pattern))


def _template_matches(template: _Template, path_segments: tuple[str, ...]) -> bool:
    if len(template.segments) != len(path_segments):
        return False
    for pattern_segment, actual_segment in zip(template.segments, path_segments, strict=True):
        if _is_placeholder(pattern_segment):
            if not actual_segment:
                return False
            continue
        if pattern_segment != actual_segment:
            return False
    return True


def _resolve_templates(path: str, candidates: Iterable[_Template]) -> PathMatch:
    matches = [candidate for candidate in candidates if _template_matches(candidate, _segments(path))]
    if not matches:
        return PathMatch(matched=False)
    specificity = max(candidate.specificity for candidate in matches)
    best = [candidate for candidate in matches if candidate.specificity == specificity]
    patterns = {candidate.pattern for candidate in best}
    if len(patterns) > 1:
        joined = ", ".join(sorted(patterns))
        raise AmbiguousPathError(
            f"ambiguous path {path!r}; equally specific patterns: {joined}"
        )
    return PathMatch(matched=True, path_pattern=best[0].pattern, exact=False)


class _LinearPathMatcher:
    def __init__(self, patterns: Iterable[str]) -> None:
        self._exact, self._templates = _compile_patterns(patterns)

    def match(self, path: object) -> PathMatch:
        normalized = normalize_origin_relative_path(path)
        if normalized in self._exact:
            return PathMatch(matched=True, path_pattern=normalized, exact=True)
        return _resolve_templates(normalized, self._templates)


@dataclass(slots=True)
class _TrieNode:
    literals: dict[str, "_TrieNode"] = field(default_factory=dict)
    wildcard: "_TrieNode | None" = None
    terminals: list[_Template] = field(default_factory=list)


class _SegmentTrieMatcher:
    def __init__(self, patterns: Iterable[str]) -> None:
        self._exact, templates = _compile_patterns(patterns)
        self._root = _TrieNode()
        for template in templates:
            node = self._root
            for segment in template.segments:
                if _is_placeholder(segment):
                    if node.wildcard is None:
                        node.wildcard = _TrieNode()
                    node = node.wildcard
                else:
                    node = node.literals.setdefault(segment, _TrieNode())
            node.terminals.append(template)

    def match(self, path: object) -> PathMatch:
        normalized = normalize_origin_relative_path(path)
        if normalized in self._exact:
            return PathMatch(matched=True, path_pattern=normalized, exact=True)
        segments = _segments(normalized)
        frontier = (self._root,)
        for segment in segments:
            next_frontier: list[_TrieNode] = []
            for node in frontier:
                literal = node.literals.get(segment)
                if literal is not None:
                    next_frontier.append(literal)
                if segment and node.wildcard is not None:
                    next_frontier.append(node.wildcard)
            if not next_frontier:
                return PathMatch(matched=False)
            frontier = tuple(next_frontier)
        candidates = [template for node in frontier for template in node.terminals]
        return _resolve_templates(normalized, candidates)


def _materialize_pattern(pattern: str, index: int) -> str:
    parts = _segments(pattern)
    materialized = [
        f"bench{index}-{segment_index}" if _is_placeholder(segment) else segment
        for segment_index, segment in enumerate(parts)
    ]
    if not materialized:
        return "/"
    return "/" + "/".join(materialized)


def _matching_workload(patterns: tuple[str, ...], requests: int) -> tuple[str, ...]:
    if not patterns:
        raise ValueError("patterns must not be empty")
    samples: list[str] = []
    for index, pattern in enumerate(patterns):
        samples.append(_materialize_pattern(pattern, index))
    samples.extend(
        (
            "/__bizman_benchmark__/missing",
            "/__bizman_benchmark__/missing/",
            "/__bizman_benchmark__/x/y/z",
        )
    )
    return tuple(samples[index % len(samples)] for index in range(requests))


def _outcome(matcher: Any, path: str) -> tuple[str, str | None, bool]:
    try:
        result = matcher.match(path)
    except AmbiguousPathError:
        return ("ambiguous", None, False)
    return ("matched" if result.matched else "unmatched", result.path_pattern, result.exact)


def benchmark_matching(
    patterns: Iterable[str],
    *,
    requests: int = 100_000,
    repeats: int = 5,
) -> dict[str, Any]:
    request_count = _positive_int(requests, name="requests")
    repeat_count = _positive_int(repeats, name="repeats")
    ordered_patterns = tuple(sorted(set(patterns)))
    workload = _matching_workload(ordered_patterns, request_count)
    matchers: dict[str, Any] = {
        "A_linear": _LinearPathMatcher(ordered_patterns),
        "B_bucketed_current": PathMatcher(ordered_patterns),
        "C_segment_trie": _SegmentTrieMatcher(ordered_patterns),
    }

    baseline_outcomes = tuple(_outcome(matchers["B_bucketed_current"], path) for path in workload)
    semantic_equivalence = all(
        tuple(_outcome(matcher, path) for path in workload) == baseline_outcomes
        for name, matcher in matchers.items()
        if name != "B_bucketed_current"
    )
    if not semantic_equivalence:
        raise RuntimeError("A/B/C path matchers are not semantically equivalent")

    variants: dict[str, dict[str, float | int]] = {}
    matched = sum(1 for state, _, _ in baseline_outcomes if state == "matched")
    for name, matcher in matchers.items():
        samples: list[float] = []
        for _ in range(repeat_count):
            started = time.perf_counter()
            current_matched = 0
            for path in workload:
                if matcher.match(path).matched:
                    current_matched += 1
            elapsed = time.perf_counter() - started
            if current_matched != matched:
                raise RuntimeError(f"{name} changed matched-result count while timing")
            samples.append(elapsed)
        median = statistics.median(samples)
        variants[name] = {
            "median_seconds": median,
            "min_seconds": min(samples),
            "max_seconds": max(samples),
            "requests_per_second": request_count / median,
            "matched": matched,
        }

    b_seconds = float(variants["B_bucketed_current"]["median_seconds"])
    for item in variants.values():
        item["speedup_vs_B"] = b_seconds / float(item["median_seconds"])
    trie_speedup = float(variants["C_segment_trie"]["speedup_vs_B"])
    return {
        "patterns": len(ordered_patterns),
        "requests": request_count,
        "repeats": repeat_count,
        "semantic_equivalence": True,
        "recommendation": (
            "consider_C" if trie_speedup >= _TRIE_CONSIDER_THRESHOLD else "keep_B"
        ),
        "trie_consider_threshold": _TRIE_CONSIDER_THRESHOLD,
        "variants": variants,
    }


def _uuid7(index: int) -> str:
    return f"01991c7d-a400-7000-8000-{index:012x}"


def _base_event(sequence: int, *, source: str, event_type: str, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": _uuid7(sequence + 1),
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": source,
        "event_type": event_type,
        "confidence": "inferred" if source == "system" else "observed",
    }
    value.update(extra)
    return value


def _request(
    sequence: int,
    *,
    request_id: str,
    method: str,
    path: str,
    query: dict[str, list[str]],
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="cdp.network",
        event_type="http.request",
        request_id=request_id,
        redirect_index=0,
        method=method,
        url_path=path,
        route_pattern=None,
        status_code=None,
        query=query,
        headers={},
        request_body_ref=None,
        response_body_ref=None,
        target_id="target-benchmark",
        frame_id="frame-benchmark",
        action_refs=[],
    )


def _response(
    sequence: int,
    *,
    request_id: str,
    method: str,
    path: str,
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="cdp.network",
        event_type="http.response",
        request_id=request_id,
        redirect_index=0,
        method=method,
        url_path=path,
        route_pattern=None,
        status_code=200,
        query={},
        headers={},
        request_body_ref=None,
        response_body_ref=None,
        target_id="target-benchmark",
        frame_id="frame-benchmark",
        action_refs=[],
    )


def _action(sequence: int) -> dict[str, object]:
    return _base_event(
        sequence,
        source="dom.action",
        event_type="dom.action",
        target_id="target-benchmark",
        frame_id="frame-benchmark",
        action_refs=[],
        action_kind="submit",
        is_trusted=True,
        page_path=POST_PATH,
        element_tag="form",
        element_type=None,
        element_name=None,
        element_role=None,
        safe_selector="form#benchmark",
        form_action_path=POST_PATH,
        form_method="POST",
        form_field_names=["cityId", "autoSupplyReserve", "autoRetailModifier", "$post"],
    )


def _correlation(
    sequence: int,
    *,
    action_event_id: str,
    request_event_id: str,
) -> dict[str, object]:
    return _base_event(
        sequence,
        source="system",
        event_type="correlation.action_http",
        target_id="target-benchmark",
        frame_id="frame-benchmark",
        action_refs=[action_event_id],
        action_event_id=action_event_id,
        network_event_id=request_event_id,
        correlation_status="strong",
        correlation_score=0.95,
        delta_ms=10.0,
        signals=["form-path-match", "method-match"],
    )


def _write_event(handle: Any, event: dict[str, object]) -> None:
    handle.write(
        (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )


def _write_stream_fixture(data_dir: Path, events: int) -> Path:
    if events % 20 != 0:
        raise ValueError("events must be a multiple of 20 to preserve the deterministic mix")
    event_rel = f"events/2026-09-07/{SESSION_ID}.jsonl"
    event_path = data_dir / event_rel
    event_path.parent.mkdir(parents=True, exist_ok=True)
    sequence = 0
    with event_path.open("wb") as handle:
        for chunk in range(events // 20):
            for post_index in range(2):
                action = _action(sequence)
                _write_event(handle, action)
                sequence += 1
                request_id = f"post-{chunk}-{post_index}"
                request = _request(
                    sequence,
                    request_id=request_id,
                    method="POST",
                    path=POST_PATH,
                    query={
                        "id": [str(chunk)],
                        **({"benchNovel": [str(chunk)]} if chunk % 100 == 0 else {}),
                    },
                )
                _write_event(handle, request)
                sequence += 1
                _write_event(
                    handle,
                    _response(
                        sequence,
                        request_id=request_id,
                        method="POST",
                        path=POST_PATH,
                    ),
                )
                sequence += 1
                _write_event(
                    handle,
                    _correlation(
                        sequence,
                        action_event_id=str(action["event_id"]),
                        request_event_id=str(request["event_id"]),
                    ),
                )
                sequence += 1
            for get_index in range(6):
                request_id = f"get-{chunk}-{get_index}"
                _write_event(
                    handle,
                    _request(
                        sequence,
                        request_id=request_id,
                        method="GET",
                        path=GET_PATH,
                        query={},
                    ),
                )
                sequence += 1
                _write_event(
                    handle,
                    _response(
                        sequence,
                        request_id=request_id,
                        method="GET",
                        path=GET_PATH,
                    ),
                )
                sequence += 1
    if sequence != events:
        raise RuntimeError(f"synthetic stream wrote {sequence} events, expected {events}")

    manifest_path = data_dir / "sessions" / SESSION_ID / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "session_id": SESSION_ID,
        "started_at": "2026-09-07T12:00:00Z",
        "ended_at": "2026-09-07T12:30:00Z",
        "status": "completed",
        "collector": {"name": "bizman-cdp", "version": "benchmark"},
        "browser": {"product": "Chrome", "version": "benchmark"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [event_rel],
        "artifact_count": 0,
        "warnings": [],
    }
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return event_path


def benchmark_streaming(
    repo_root: Path,
    redaction: RedactionPolicy,
    *,
    events: int = 100_000,
    work_root: Path | None = None,
) -> dict[str, Any]:
    event_count = _positive_int(events, name="events")
    if event_count % 20 != 0:
        raise ValueError("events must be a multiple of 20 to preserve the deterministic mix")
    root = Path(repo_root).expanduser().resolve()
    if not isinstance(redaction, RedactionPolicy):
        raise TypeError("redaction must be RedactionPolicy")

    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if work_root is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="bizman-detector-benchmark-")
        base = Path(owned_temp.name)
    else:
        base = Path(work_root).expanduser().resolve(strict=False)
        base.mkdir(parents=True, exist_ok=True)
    try:
        data_dir = base / "BizManData"
        event_path = _write_stream_fixture(data_dir, event_count)
        compilation = BaselineCompiler.compile(root, redaction)
        reader = EvidenceReader(root, data_dir)
        extractor = ObservationExtractor(compilation.contract, reader, redaction)

        tracemalloc.start()
        started = time.perf_counter()
        try:
            identity = reader.inspect(SESSION_ID)
            observations = extractor.extract(identity)
            elapsed = time.perf_counter() - started
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        return {
            "source_events": event_count,
            "validated_event_passes": _VALIDATION_PASSES,
            "elapsed_seconds": elapsed,
            "source_events_per_second": event_count / elapsed,
            "validation_events_per_second": (_VALIDATION_PASSES * event_count) / elapsed,
            "peak_tracemalloc_bytes": peak_bytes,
            "event_file_bytes": event_path.stat().st_size,
            "evidence_sha256": identity.evidence_sha256,
            "observations": {
                "http": len(observations.http),
                "forms": len(observations.forms),
                "relations": len(observations.relations),
            },
        }
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-gating A/B/C path matcher and detector streaming benchmark."
    )
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--match-requests", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.expanduser().resolve()
    redaction = load_redaction_policy(repo_root / "config" / "redaction-policy.json")
    compilation = BaselineCompiler.compile(repo_root, redaction)
    patterns = tuple(endpoint.path_pattern for endpoint in compilation.contract.endpoints)

    matching = benchmark_matching(
        patterns,
        requests=args.match_requests,
        repeats=args.repeats,
    )
    streaming = benchmark_streaming(
        repo_root,
        redaction,
        events=args.events,
    )
    print(
        json.dumps(
            {
                "benchmark_version": 1,
                "implementation": {
                    "matching": "tools.bizman_detector.normalization.PathMatcher",
                    "streaming": "EvidenceReader+ObservationExtractor",
                },
                "matching": matching,
                "streaming": streaming,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
