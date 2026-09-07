#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile
import time


def _payload(sequence: int) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "event_id": "01991c7d-a400-7000-8000-000000000001",
            "session_id": "01991c7d-a400-7000-8000-000000000002",
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
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _run_once(path: Path, *, events: int, flush_every: int) -> float:
    started = time.perf_counter()
    with path.open("w", encoding="utf-8", buffering=1024 * 1024) as handle:
        for sequence in range(events):
            handle.write(_payload(sequence))
            if flush_every > 0 and (sequence + 1) % flush_every == 0:
                handle.flush()
        handle.flush()
        os.fsync(handle.fileno())
    return time.perf_counter() - started


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
    results: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, flush_every in variants.items():
            samples = [
                _run_once(
                    root / f"{name}-{repeat}.jsonl",
                    events=args.events,
                    flush_every=flush_every,
                )
                for repeat in range(args.repeats)
            ]
            median = statistics.median(samples)
            results[name] = {
                "median_seconds": median,
                "events_per_second": args.events / median,
                "min_seconds": min(samples),
                "max_seconds": max(samples),
            }

    baseline = results["A_flush_each"]["median_seconds"]
    for item in results.values():
        item["speedup_vs_A"] = baseline / item["median_seconds"]

    print(json.dumps({"events": args.events, "repeats": args.repeats, "variants": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
