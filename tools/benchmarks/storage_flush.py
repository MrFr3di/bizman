#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bizman_collector.storage import SessionWriter

SESSION_ID = "01991c7d-a400-7000-8000-000000000002"


def _manifest() -> dict:
    return {
        "schema_version": "1.0",
        "session_id": SESSION_ID,
        "started_at": "2026-09-07T00:00:00Z",
        "ended_at": None,
        "status": "running",
        "collector": {"name": "bizman-cdp", "version": "benchmark"},
        "browser": {"product": "Chrome", "version": "benchmark"},
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


def _event(sequence: int) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "01991c7d-a400-7000-8000-000000000001",
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T00:00:00Z",
        "monotonic_time": float(sequence),
        "source": "cdp.network",
        "event_type": "http.request",
        "confidence": "observed",
        "request_id": f"r{sequence}",
        "method": "GET",
        "url_path": "/benchmark",
        "query": {"id": [str(sequence)]},
        "headers": {"Accept": "application/json"},
    }


def _run_once(root: Path, *, events: int, flush_every: int) -> tuple[float, int]:
    writer = SessionWriter(
        root,
        _manifest(),
        flush_every_events=flush_every,
    )
    started = time.perf_counter()
    for sequence in range(events):
        writer.append_event(_event(sequence))
    writer.finalize()
    elapsed = time.perf_counter() - started

    lines = sum(1 for _ in writer.event_path.open("r", encoding="utf-8"))
    if lines != events:
        raise RuntimeError(
            f"SessionWriter benchmark lost events: expected {events}, found {lines}"
        )
    return elapsed, writer.event_path.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=25000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.events <= 0 or args.repeats <= 0:
        raise SystemExit("--events and --repeats must be positive")

    variants = {
        "A_flush_each": 1,
        "B_flush_64": 64,
        "C_flush_512": 512,
    }
    results: dict[str, dict[str, float | int]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, flush_every in variants.items():
            samples: list[float] = []
            output_bytes: list[int] = []
            for repeat in range(args.repeats):
                elapsed, size = _run_once(
                    root / name / str(repeat),
                    events=args.events,
                    flush_every=flush_every,
                )
                samples.append(elapsed)
                output_bytes.append(size)
            if len(set(output_bytes)) != 1:
                raise RuntimeError(
                    f"non-deterministic output size for {name}: {output_bytes}"
                )
            median = statistics.median(samples)
            results[name] = {
                "flush_every_events": flush_every,
                "median_seconds": median,
                "events_per_second": args.events / median,
                "min_seconds": min(samples),
                "max_seconds": max(samples),
                "output_bytes": output_bytes[0],
            }

    baseline = float(results["A_flush_each"]["median_seconds"])
    for item in results.values():
        item["speedup_vs_A"] = baseline / float(item["median_seconds"])

    print(
        json.dumps(
            {
                "implementation": "tools.bizman_collector.storage.SessionWriter",
                "events": args.events,
                "repeats": args.repeats,
                "variants": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
