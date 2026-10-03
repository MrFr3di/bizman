"""``python -m bizman.ingest`` command line entry point."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from bizman.ingest.datasets import DATASET_LAYOUTS, build_dataset, write_dataset
from bizman.ingest.har import HarEntry, HarFormatError, load_har

_MAX_DISPLAY_CHARS = 160


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bizman.ingest",
        description=(
            "Generate the committed HAR-derived datasets into an output "
            "directory and optionally compare them with the golden corpus."
        ),
    )
    parser.add_argument(
        "--har",
        action="append",
        required=True,
        type=Path,
        metavar="PATH",
        help="HAR capture to read; repeat for multiple captures",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="DIR",
        help="output directory for generated datasets",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        choices=list(DATASET_LAYOUTS),
        dest="datasets",
        metavar="ID",
        help="dataset to generate; repeat to select a subset",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        metavar="KNOWLEDGE_DIR",
        help="compare generated records with a committed knowledge directory",
    )
    args = parser.parse_args(argv)

    selected = args.datasets or list(DATASET_LAYOUTS)

    captures: dict[str, tuple[HarEntry, ...]] = {}
    for path in args.har:
        alias = path.name
        if alias in captures:
            print(f"error: duplicate capture name: {alias}", file=sys.stderr)
            return 2
        try:
            captures[alias] = load_har(path)
        except HarFormatError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    generated: dict[str, list[dict[str, Any]]] = {}
    for dataset_id in selected:
        records = build_dataset(dataset_id, captures)
        layout = DATASET_LAYOUTS[dataset_id]
        write_dataset(
            args.out,
            dataset_id,
            records,
            jsonl=bool(layout["jsonl"]),
            wrapper_key=layout["wrapper_key"],
        )
        generated[dataset_id] = records

    if args.compare is None:
        return 0

    all_matched = True
    for dataset_id in selected:
        try:
            committed = _read_committed(args.compare, dataset_id)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"{dataset_id}: cannot read committed corpus: {exc}")
            all_matched = False
            continue
        records = generated[dataset_id]
        mismatches = _differences(records, committed)
        if not mismatches:
            print(
                f"{dataset_id}: generated={len(records)} "
                f"committed={len(committed)} match"
            )
            continue
        all_matched = False
        differing = sum(count for _, _, count in mismatches)
        print(
            f"{dataset_id}: generated={len(records)} "
            f"committed={len(committed)} MISMATCH differing={differing}"
        )
        for side, record, count in mismatches[:3]:
            print(f"  {side} x{count}: {_bounded(record)}")

    return 0 if all_matched else 1


def _read_committed(knowledge_dir: Path, dataset_id: str) -> list[dict[str, Any]]:
    layout = DATASET_LAYOUTS[dataset_id]
    base = Path(knowledge_dir) / str(layout["knowledge_dir"])
    manifest = json.loads((base / "index.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for part in manifest["parts"]:
        name = part["file"]
        text = (base / name).read_text(encoding="utf-8")
        if name.endswith(".jsonl"):
            for line in text.splitlines():
                if line.strip():
                    records.append(json.loads(line))
        else:
            payload = json.loads(text)
            if isinstance(payload, list):
                records.extend(payload)
            else:
                key = layout["wrapper_key"] or "records"
                records.extend(payload[key])
    return records


def _key(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _differences(
    generated: Sequence[dict[str, Any]],
    committed: Sequence[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], int]]:
    generated_counts = Counter(_key(record) for record in generated)
    committed_counts = Counter(_key(record) for record in committed)
    differences: list[tuple[str, dict[str, Any], int]] = []
    seen: set[str] = set()
    for record in generated:
        key = _key(record)
        if key in seen:
            continue
        seen.add(key)
        count = generated_counts[key] - committed_counts[key]
        if count > 0:
            differences.append(("generated-only", json.loads(key), count))
    seen.clear()
    for record in committed:
        key = _key(record)
        if key in seen:
            continue
        seen.add(key)
        count = committed_counts[key] - generated_counts[key]
        if count > 0:
            differences.append(("committed-only", json.loads(key), count))
    return differences


def _bounded(record: dict[str, Any]) -> str:
    text = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if len(text) <= _MAX_DISPLAY_CHARS:
        return text
    return text[:_MAX_DISPLAY_CHARS] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
