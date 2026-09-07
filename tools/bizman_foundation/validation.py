from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover - surfaced as a validation error
    Draft202012Validator = None  # type: ignore[assignment]
    FormatChecker = None  # type: ignore[assignment]


@dataclass(slots=True)
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_json(path: Path, result: ValidationResult) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        result.errors.append(f"missing file: {path}")
    except json.JSONDecodeError as exc:
        result.errors.append(f"invalid JSON: {path}: {exc}")
    except UnicodeDecodeError as exc:
        result.errors.append(f"invalid UTF-8: {path}: {exc}")
    return None


def _iter_jsonl(path: Path, result: ValidationResult) -> Iterable[Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    result.errors.append(f"invalid JSONL: {path}:{lineno}: {exc}")
    except FileNotFoundError:
        result.errors.append(f"missing file: {path}")
    except UnicodeDecodeError as exc:
        result.errors.append(f"invalid UTF-8: {path}: {exc}")


def _count_records(path: Path, result: ValidationResult) -> int | None:
    if path.suffix == ".jsonl":
        return sum(1 for _ in _iter_jsonl(path, result))
    obj = _load_json(path, result)
    if obj is None:
        return None
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in (
            "records",
            "items",
            "scripts",
            "captures",
            "entities",
            "parameters",
            "forms",
            "navigation_topics",
        ):
            value = obj.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, int) and key == "navigation_topics":
                return value
    result.errors.append(f"cannot infer record count: {path}")
    return None


def _validate_partition_manifest(
    path: Path, expected: int | None, result: ValidationResult
) -> None:
    manifest = _load_json(path, result)
    if not isinstance(manifest, dict):
        return
    total = manifest.get("total_records", manifest.get("total_count"))
    if not isinstance(total, int):
        result.errors.append(f"{path}: missing integer total_records/total_count")
        return
    if expected is not None and total != expected:
        result.errors.append(f"{path}: catalog says {expected}, manifest says {total}")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        result.errors.append(f"{path}: missing parts")
        return

    declared_sum = 0
    expected_offset = 0
    for part in parts:
        if not isinstance(part, dict):
            result.errors.append(f"{path}: invalid part descriptor {part!r}")
            continue
        name = part.get("file", part.get("path"))
        declared = part.get("records", part.get("count"))
        if not isinstance(name, str) or not isinstance(declared, int):
            result.errors.append(f"{path}: invalid part descriptor {part!r}")
            continue
        if part.get("offset", expected_offset) != expected_offset:
            result.errors.append(
                f"{path}: {name} offset {part.get('offset')} != {expected_offset}"
            )
        expected_offset += declared
        declared_sum += declared
        actual = _count_records(path.parent / name, result)
        if actual is not None and actual != declared:
            result.errors.append(
                f"{path}: {name} declares {declared}, contains {actual}"
            )

    if declared_sum != total:
        result.errors.append(
            f"{path}: parts sum to {declared_sum}, manifest total is {total}"
        )


def _validate_catalog(root: Path, result: ValidationResult) -> None:
    catalog_path = root / "knowledge/catalog.json"
    catalog = _load_json(catalog_path, result)
    if not isinstance(catalog, dict):
        return
    datasets = catalog.get("datasets")
    if not isinstance(datasets, list):
        result.errors.append(f"{catalog_path}: datasets must be an array")
        return

    seen_ids: set[str] = set()
    for item in datasets:
        if not isinstance(item, dict):
            result.errors.append(f"{catalog_path}: invalid dataset entry {item!r}")
            continue
        dataset_id = item.get("id")
        dataset_path = item.get("path")
        expected = item.get("records")
        fmt = item.get("format", "")
        if not isinstance(dataset_id, str) or not dataset_id:
            result.errors.append(f"{catalog_path}: dataset missing id: {item!r}")
            continue
        if dataset_id in seen_ids:
            result.errors.append(f"{catalog_path}: duplicate dataset id: {dataset_id}")
        seen_ids.add(dataset_id)
        if not isinstance(dataset_path, str):
            result.errors.append(f"{catalog_path}: dataset {dataset_id} missing path")
            continue
        path = root / dataset_path
        if not path.exists():
            result.errors.append(
                f"{catalog_path}: dataset path does not exist: {dataset_path}"
            )
            continue
        if expected is not None and not isinstance(expected, int):
            result.errors.append(
                f"{catalog_path}: dataset {dataset_id} records must be integer"
            )
            expected = None
        if isinstance(fmt, str) and fmt.startswith("partition-manifest/"):
            _validate_partition_manifest(path, expected, result)
        elif isinstance(expected, int):
            actual = _count_records(path, result)
            if actual is not None and actual != expected:
                result.errors.append(
                    f"{dataset_path}: catalog declares {expected}, contains {actual}"
                )


def _validate_schemas(root: Path, result: ValidationResult) -> None:
    schema_dir = root / "schemas"
    if not schema_dir.exists():
        return
    if Draft202012Validator is None:
        result.errors.append(
            "jsonschema dependency is required for Draft 2020-12 validation"
        )
        return
    for path in sorted(schema_dir.glob("*.schema.json")):
        schema = _load_json(path, result)
        if not isinstance(schema, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # jsonschema raises SchemaError subclasses
            result.errors.append(f"invalid JSON Schema: {path}: {exc}")


def _validate_instance_against_schema(
    instance_path: Path, schema_path: Path, result: ValidationResult
) -> None:
    if (
        Draft202012Validator is None
        or FormatChecker is None
        or not schema_path.exists()
        or not instance_path.exists()
    ):
        return
    schema = _load_json(schema_path, result)
    instance = _load_json(instance_path, result)
    if not isinstance(schema, dict) or instance is None:
        return
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(
        validator.iter_errors(instance), key=lambda item: list(item.absolute_path)
    ):
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        result.errors.append(
            f"{instance_path}: violates {schema_path.name} at {location}: "
            f"{error.message}"
        )


def _validate_known_instances(root: Path, result: ValidationResult) -> None:
    pairs = (
        (
            root / "knowledge/sources/captures.json",
            root / "schemas/capture-index.schema.json",
        ),
        (
            root / "config/redaction-policy.json",
            root / "schemas/redaction-policy.schema.json",
        ),
    )
    for instance_path, schema_path in pairs:
        if instance_path.exists() and schema_path.exists():
            _validate_instance_against_schema(instance_path, schema_path, result)


def _validate_source_identity(root: Path, result: ValidationResult) -> None:
    source_dir = root / "knowledge/sources"
    if not source_dir.exists():
        return
    manifests: dict[str, dict[str, Any]] = {}
    canonical_ids: set[str] = set()
    source_schema = root / "schemas/source.schema.json"
    for path in sorted(source_dir.glob("*.har.json")):
        _validate_instance_against_schema(path, source_schema, result)
        obj = _load_json(path, result)
        if not isinstance(obj, dict):
            continue
        source_id = obj.get("id")
        filename = obj.get("source_filename")
        if not isinstance(source_id, str) or not source_id.startswith("src."):
            result.errors.append(f"{path}: invalid canonical source id")
            continue
        if source_id in canonical_ids:
            result.errors.append(f"{path}: duplicate canonical source id {source_id}")
        canonical_ids.add(source_id)
        if isinstance(filename, str):
            if filename in manifests:
                result.errors.append(f"{path}: duplicate source filename {filename}")
            manifests[filename] = obj

    captures_path = source_dir / "captures.json"
    if not captures_path.exists():
        return
    captures = _load_json(captures_path, result)
    if not isinstance(captures, dict) or not isinstance(captures.get("captures"), list):
        return

    aliases: set[str] = set()
    for capture in captures["captures"]:
        if not isinstance(capture, dict):
            continue
        filename = capture.get("file_name")
        source_id = capture.get("source_id")
        manifest = manifests.get(filename) if isinstance(filename, str) else None
        if manifest is None:
            result.errors.append(
                f"{captures_path}: no source manifest for {filename!r}"
            )
            continue
        if source_id != manifest.get("id"):
            result.errors.append(
                f"{captures_path}: {filename} source_id {source_id!r} "
                f"!= {manifest.get('id')!r}"
            )
        legacy = capture.get("legacy_source_ids", [])
        if not isinstance(legacy, list) or not all(
            isinstance(value, str) for value in legacy
        ):
            result.errors.append(
                f"{captures_path}: {filename} legacy_source_ids must be strings"
            )
            continue
        for alias in legacy:
            if alias in aliases or alias in canonical_ids:
                result.errors.append(f"{captures_path}: duplicate source alias {alias}")
            aliases.add(alias)


def _scan_for_forbidden_files(root: Path, result: ValidationResult) -> None:
    forbidden_suffixes = {
        ".har",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".parquet",
        ".duckdb",
    }
    forbidden_directories = {
        "browser-profile",
        "chrome-profile",
        "bizmandata",
        "data",
        "local-data",
        "raw",
        "artifacts",
    }
    sensitive_json_prefixes = (
        "cookies",
        "auth",
        "storage-state",
        "storage_state",
        "storagestate",
        "session-state",
        "session_state",
    )

    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(root)
        lower_name = path.name.casefold()
        lower_parts = tuple(part.casefold() for part in relative.parts[:-1])
        has_forbidden_dir = any(part in forbidden_directories for part in lower_parts)
        is_env_file = lower_name == ".env" or lower_name.startswith(".env.")
        is_sensitive_json_export = (
            lower_name.endswith(".json")
            and not lower_name.endswith(".schema.json")
            and lower_name.startswith(sensitive_json_prefixes)
        )
        if (
            path.suffix.lower() in forbidden_suffixes
            or is_env_file
            or is_sensitive_json_export
            or has_forbidden_dir
        ):
            result.errors.append(f"forbidden committed file: {relative}")


def validate_repository(root: Path) -> ValidationResult:
    root = root.resolve()
    result = ValidationResult()
    _validate_catalog(root, result)
    _validate_schemas(root, result)
    _validate_known_instances(root, result)
    _validate_source_identity(root, result)
    _scan_for_forbidden_files(root, result)
    return result
