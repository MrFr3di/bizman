#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        fail(f"missing file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {path.relative_to(ROOT)}: {exc}")
    return None


def count_jsonl(path: Path) -> int | None:
    if not path.is_file():
        fail(f"missing file: {path.relative_to(ROOT)}")
        return None
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                count += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    fail(f"invalid JSONL: {path.relative_to(ROOT)}:{lineno}: {exc}")
    except UnicodeDecodeError as exc:
        fail(f"invalid UTF-8: {path.relative_to(ROOT)}: {exc}")
    return count


def count_records(path: Path) -> int | None:
    if path.suffix == ".jsonl":
        return count_jsonl(path)
    obj = load_json(path)
    if obj is None:
        return None
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in ("records", "items", "scripts", "captures", "entities", "parameters", "forms"):
            value = obj.get(key)
            if isinstance(value, list):
                return len(value)
    fail(f"cannot infer record count: {path.relative_to(ROOT)}")
    return None


def validate_manifest(rel: str, expected: int) -> None:
    path = ROOT / rel
    manifest = load_json(path)
    if not isinstance(manifest, dict):
        return
    total = manifest.get("total_records", manifest.get("total_count"))
    if total != expected:
        fail(f"{rel}: expected total {expected}, manifest says {total}")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        fail(f"{rel}: missing parts")
        return

    declared_sum = 0
    expected_offset = 0
    for part in parts:
        if not isinstance(part, dict):
            fail(f"{rel}: invalid part entry")
            continue
        name = part.get("file", part.get("path"))
        declared = part.get("records", part.get("count"))
        if not isinstance(name, str) or not isinstance(declared, int):
            fail(f"{rel}: invalid part descriptor {part!r}")
            continue
        declared_sum += declared
        if "offset" in part and part["offset"] != expected_offset:
            fail(f"{rel}: {name} offset {part['offset']} != {expected_offset}")
        expected_offset += declared
        actual = count_records(path.parent / name)
        if actual is not None and actual != declared:
            fail(f"{rel}: {name} declares {declared}, contains {actual}")

    if declared_sum != expected:
        fail(f"{rel}: parts sum to {declared_sum}, expected {expected}")


def validate_exact_count(rel: str, expected: int, key: str | None = None) -> None:
    path = ROOT / rel
    if path.suffix == ".jsonl":
        actual = count_jsonl(path)
    else:
        obj = load_json(path)
        if obj is None:
            return
        if key is None:
            actual = count_records(path)
        elif isinstance(obj, dict) and isinstance(obj.get(key), list):
            actual = len(obj[key])
        else:
            fail(f"{rel}: expected list key {key!r}")
            return
    if actual is not None and actual != expected:
        fail(f"{rel}: expected {expected} records, found {actual}")


def validate_catalog() -> None:
    rel = "knowledge/catalog.json"
    catalog = load_json(ROOT / rel)
    if not isinstance(catalog, dict):
        return
    if catalog.get("source_capture_count") != 3:
        fail(f"{rel}: source_capture_count must be 3")
    if catalog.get("network_entry_count") != 17108:
        fail(f"{rel}: network_entry_count must be 17108")
    if catalog.get("first_party_entry_count") != 16202:
        fail(f"{rel}: first_party_entry_count must be 16202")
    datasets = catalog.get("datasets", [])
    if not isinstance(datasets, list):
        fail(f"{rel}: datasets must be an array")
        return
    for item in datasets:
        if not isinstance(item, dict):
            fail(f"{rel}: invalid dataset entry")
            continue
        dataset_path = item.get("path")
        if not isinstance(dataset_path, str):
            fail(f"{rel}: dataset missing path: {item!r}")
            continue
        if not (ROOT / dataset_path).exists():
            fail(f"{rel}: dataset path does not exist: {dataset_path}")


def scan_for_forbidden_files() -> None:
    forbidden_suffixes = {".har", ".sqlite", ".sqlite3", ".db"}
    forbidden_names = {".env", "cookies.json", "auth.json", "storageState.json"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() in forbidden_suffixes or path.name in forbidden_names:
            fail(f"forbidden committed file: {path.relative_to(ROOT)}")


def main() -> int:
    validate_catalog()

    manifests = {
        "knowledge/http/application-events/index.json": 555,
        "knowledge/http/assets/index.json": 914,
        "knowledge/http/endpoints/index.json": 68,
        "knowledge/http/forms/index.json": 87,
        "knowledge/http/json-responses/index.json": 36,
        "knowledge/http/routes/index.json": 732,
        "knowledge/pages/index.json": 180,
        "knowledge/wiki/topics/index.json": 87,
        "knowledge/domain/products/index.json": 303,
    }
    for rel, expected in manifests.items():
        validate_manifest(rel, expected)

    validate_exact_count("knowledge/sources/captures.json", 3, "captures")
    validate_exact_count("knowledge/http/post-observations.jsonl", 17)
    validate_exact_count("knowledge/javascript/script-index.json", 29, "scripts")
    validate_exact_count("knowledge/javascript/relevant-snippets.jsonl", 14)
    validate_exact_count("knowledge/actions/catalog.json", 8, "items")
    validate_exact_count("knowledge/domain/entities.json", 18, "entities")
    validate_exact_count("knowledge/forms/parameters.json", 62, "parameters")

    nav = load_json(ROOT / "knowledge/wiki/navigation.json")
    if isinstance(nav, dict):
        if len(nav.get("captured_topics", [])) != 87:
            fail("knowledge/wiki/navigation.json: captured_topics != 87")
        if len(nav.get("navigation_topics", [])) != 89:
            fail("knowledge/wiki/navigation.json: navigation_topics != 89")

    scan_for_forbidden_files()

    if ERRORS:
        print("VALIDATION FAILED", file=sys.stderr)
        for error in ERRORS:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("VALIDATION OK")
    print("All manifests, record counts, catalog paths and forbidden-file checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
