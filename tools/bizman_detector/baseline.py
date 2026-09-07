from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.bizman_detector import CONTRACT_SCHEMA_VERSION, NORMALIZATION_VERSION
from tools.bizman_detector.model import (
    ActionRequestFamily,
    EndpointFamily,
    EndpointMethodContract,
    EndpointVariant,
    FormSignature,
    OperationSignature,
    RuntimeContract,
)
from tools.bizman_detector.normalization import (
    AmbiguousPathError,
    PathMatcher,
    normalize_key_set,
    normalize_method,
    normalize_origin_relative_path,
)
from tools.bizman_foundation.fingerprint import canonical_sha256
from tools.bizman_foundation.redaction import RedactionPolicy


class BaselineError(ValueError):
    """Base class for curated-baseline failures."""


class BaselineFormatError(BaselineError):
    """Curated files are malformed, incomplete, or unsafe to read."""


class BaselineConsistencyError(BaselineError):
    """Curated datasets disagree about the same semantic evidence."""


@dataclass(frozen=True, slots=True)
class BaselineCompilation:
    contract: RuntimeContract
    baseline_sha256: str
    redaction_policy_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    value: Any
    source: str


@dataclass(frozen=True, slots=True)
class _EndpointAggregate:
    path_pattern: str
    count: int
    method_counts: tuple[tuple[str, int], ...]
    status_counts: tuple[tuple[int, int], ...]
    query_keys: tuple[str, ...]
    source: str


@dataclass(slots=True)
class _ObservedEndpoint:
    total_count: int
    method_counts: Counter[str]
    status_counts: Counter[int]
    query_keys: set[str]
    variants_by_method: dict[str, set[EndpointVariant]]

    @classmethod
    def empty(cls) -> "_ObservedEndpoint":
        return cls(
            total_count=0,
            method_counts=Counter(),
            status_counts=Counter(),
            query_keys=set(),
            variants_by_method=defaultdict(set),
        )


def _source_label(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _load_json(path: Path, *, repo_root: Path) -> Any:
    label = _source_label(repo_root, path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise BaselineFormatError(f"missing curated file: {label}") from exc
    except UnicodeDecodeError as exc:
        raise BaselineFormatError(f"invalid UTF-8 in {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineFormatError(f"invalid JSON in {label}: {exc}") from exc


def _require_non_negative_int(value: object, *, source: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BaselineFormatError(f"{source}: {field} must be a non-negative integer")
    return value


def _resolve_declared_part(dataset_dir: Path, name: object, *, source: str) -> Path:
    if not isinstance(name, str) or not name:
        raise BaselineFormatError(f"{source}: part file must be a non-empty string")
    if Path(name).is_absolute():
        raise BaselineFormatError(f"{source}: declared part {name!r} escapes dataset directory")
    dataset_root = dataset_dir.resolve()
    candidate = (dataset_dir / name).resolve()
    if not candidate.is_relative_to(dataset_root):
        raise BaselineFormatError(f"{source}: declared part {name!r} escapes dataset directory")
    return candidate


def _load_partitioned_json(
    repo_root: Path,
    index_path: Path,
) -> tuple[_SourceRecord, ...]:
    index = _load_json(index_path, repo_root=repo_root)
    index_label = _source_label(repo_root, index_path)
    if not isinstance(index, Mapping):
        raise BaselineFormatError(f"{index_label}: partition index must be an object")
    total = _require_non_negative_int(
        index.get("total_records"), source=index_label, field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list) or not parts:
        raise BaselineFormatError(f"{index_label}: parts must be a non-empty array")

    default_key = index.get("record_key", index.get("list_key"))
    if default_key is not None and not isinstance(default_key, str):
        raise BaselineFormatError(f"{index_label}: record/list key must be a string")

    records: list[_SourceRecord] = []
    seen_parts: set[Path] = set()
    expected_offset = 0
    for descriptor_index, descriptor in enumerate(parts):
        if not isinstance(descriptor, Mapping):
            raise BaselineFormatError(
                f"{index_label}: part descriptor {descriptor_index} must be an object"
            )
        part_path = _resolve_declared_part(
            index_path.parent,
            descriptor.get("file", descriptor.get("path")),
            source=index_label,
        )
        if part_path in seen_parts:
            raise BaselineFormatError(
                f"{index_label}: duplicate part {part_path.name!r}"
            )
        seen_parts.add(part_path)

        declared = _require_non_negative_int(
            descriptor.get("records", descriptor.get("count")),
            source=index_label,
            field=f"parts[{descriptor_index}].records",
        )
        if "offset" in descriptor:
            offset = _require_non_negative_int(
                descriptor.get("offset"),
                source=index_label,
                field=f"parts[{descriptor_index}].offset",
            )
            if offset != expected_offset:
                raise BaselineFormatError(
                    f"{index_label}: {part_path.name} offset {offset} != {expected_offset}"
                )

        payload = _load_json(part_path, repo_root=repo_root)
        if isinstance(payload, list):
            part_records = payload
        elif isinstance(payload, Mapping):
            key = default_key or "records"
            part_records = payload.get(key)
            if not isinstance(part_records, list):
                raise BaselineFormatError(
                    f"{_source_label(repo_root, part_path)}: {key!r} must be an array"
                )
        else:
            raise BaselineFormatError(
                f"{_source_label(repo_root, part_path)}: partition must be an array or object"
            )

        if len(part_records) != declared:
            raise BaselineFormatError(
                f"{_source_label(repo_root, part_path)} declares {declared} records "
                f"but contains {len(part_records)}"
            )
        for record_index, value in enumerate(part_records):
            records.append(
                _SourceRecord(
                    value=value,
                    source=f"{_source_label(repo_root, part_path)}#record-{record_index}",
                )
            )
        expected_offset += declared

    if expected_offset != total:
        raise BaselineFormatError(
            f"{index_label}: parts contain {expected_offset} records, total_records is {total}"
        )
    return tuple(records)


def _load_partitioned_jsonl(
    repo_root: Path,
    index_path: Path,
) -> tuple[_SourceRecord, ...]:
    index = _load_json(index_path, repo_root=repo_root)
    index_label = _source_label(repo_root, index_path)
    if not isinstance(index, Mapping):
        raise BaselineFormatError(f"{index_label}: partition index must be an object")
    total = _require_non_negative_int(
        index.get("total_records"), source=index_label, field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list) or not parts:
        raise BaselineFormatError(f"{index_label}: parts must be a non-empty array")

    records: list[_SourceRecord] = []
    seen_parts: set[Path] = set()
    expected_offset = 0
    for descriptor_index, descriptor in enumerate(parts):
        if not isinstance(descriptor, Mapping):
            raise BaselineFormatError(
                f"{index_label}: part descriptor {descriptor_index} must be an object"
            )
        part_path = _resolve_declared_part(
            index_path.parent,
            descriptor.get("file", descriptor.get("path")),
            source=index_label,
        )
        if part_path in seen_parts:
            raise BaselineFormatError(
                f"{index_label}: duplicate part {part_path.name!r}"
            )
        seen_parts.add(part_path)
        declared = _require_non_negative_int(
            descriptor.get("records", descriptor.get("count")),
            source=index_label,
            field=f"parts[{descriptor_index}].records",
        )
        if "offset" in descriptor:
            offset = _require_non_negative_int(
                descriptor.get("offset"),
                source=index_label,
                field=f"parts[{descriptor_index}].offset",
            )
            if offset != expected_offset:
                raise BaselineFormatError(
                    f"{index_label}: {part_path.name} offset {offset} != {expected_offset}"
                )

        label = _source_label(repo_root, part_path)
        actual = 0
        try:
            with part_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    actual += 1
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise BaselineFormatError(
                            f"invalid JSONL in {label}:{line_number}: {exc}"
                        ) from exc
                    records.append(
                        _SourceRecord(value=value, source=f"{label}:{line_number}")
                    )
        except FileNotFoundError as exc:
            raise BaselineFormatError(f"missing curated file: {label}") from exc
        except UnicodeDecodeError as exc:
            raise BaselineFormatError(f"invalid UTF-8 in {label}: {exc}") from exc

        if actual != declared:
            raise BaselineFormatError(
                f"{label} declares {declared} records but contains {actual}"
            )
        expected_offset += declared

    if expected_offset != total:
        raise BaselineFormatError(
            f"{index_label}: parts contain {expected_offset} records, total_records is {total}"
        )
    return tuple(records)


def _normalize_status(value: object, *, source: str) -> int:
    if isinstance(value, bool):
        raise BaselineFormatError(f"{source}: status must be an integer in 100..599")
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 100 <= value <= 599:
        raise BaselineFormatError(f"{source}: status must be an integer in 100..599")
    return value


def _normalized_count_mapping(
    value: object,
    *,
    source: str,
    kind: str,
) -> tuple[tuple[Any, int], ...]:
    if not isinstance(value, Mapping):
        raise BaselineFormatError(f"{source}: {kind} must be an object")
    normalized: dict[Any, int] = {}
    for raw_key, raw_count in value.items():
        key: Any
        if kind == "methods":
            try:
                key = normalize_method(raw_key)
            except ValueError as exc:
                raise BaselineFormatError(f"{source}: invalid method {raw_key!r}") from exc
        else:
            key = _normalize_status(raw_key, source=source)
        count = _require_non_negative_int(raw_count, source=source, field=f"{kind}.{raw_key}")
        if count == 0:
            raise BaselineFormatError(f"{source}: {kind}.{raw_key} count must be positive")
        if key in normalized:
            raise BaselineFormatError(f"{source}: duplicate normalized {kind} key {key!r}")
        normalized[key] = count
    if not normalized:
        raise BaselineFormatError(f"{source}: {kind} must not be empty")
    return tuple(sorted(normalized.items()))


def _compile_endpoint_aggregates(
    records: tuple[_SourceRecord, ...],
    redaction: RedactionPolicy,
) -> tuple[_EndpointAggregate, ...]:
    aggregates: list[_EndpointAggregate] = []
    seen_patterns: set[str] = set()
    for record in records:
        value = record.value
        if not isinstance(value, Mapping):
            raise BaselineFormatError(f"{record.source}: endpoint record must be an object")
        try:
            pattern = normalize_origin_relative_path(value.get("path_pattern"))
        except ValueError as exc:
            raise BaselineFormatError(f"{record.source}: invalid path_pattern") from exc
        if pattern in seen_patterns:
            raise BaselineConsistencyError(
                f"{record.source}: duplicate endpoint path_pattern {pattern!r}"
            )
        seen_patterns.add(pattern)
        count = _require_non_negative_int(value.get("count"), source=record.source, field="count")
        if count == 0:
            raise BaselineFormatError(f"{record.source}: endpoint count must be positive")
        method_counts = _normalized_count_mapping(
            value.get("methods"), source=record.source, kind="methods"
        )
        status_counts = _normalized_count_mapping(
            value.get("statuses"), source=record.source, kind="statuses"
        )
        query_keys = value.get("query_keys")
        if not isinstance(query_keys, list):
            raise BaselineFormatError(f"{record.source}: query_keys must be an array")
        normalized_query_keys = normalize_key_set(query_keys, redaction)
        if sum(item[1] for item in method_counts) != count:
            raise BaselineConsistencyError(
                f"{record.source}: method counts do not sum to endpoint count {count}"
            )
        if sum(item[1] for item in status_counts) != count:
            raise BaselineConsistencyError(
                f"{record.source}: status counts do not sum to endpoint count {count}"
            )
        aggregates.append(
            _EndpointAggregate(
                path_pattern=pattern,
                count=count,
                method_counts=method_counts,
                status_counts=status_counts,
                query_keys=normalized_query_keys,
                source=record.source,
            )
        )
    return tuple(sorted(aggregates, key=lambda item: item.path_pattern))


def _compile_endpoint_families(
    aggregates: tuple[_EndpointAggregate, ...],
    application_events: tuple[_SourceRecord, ...],
    redaction: RedactionPolicy,
) -> tuple[EndpointFamily, ...]:
    matcher = PathMatcher(item.path_pattern for item in aggregates)
    observed: dict[str, _ObservedEndpoint] = {
        item.path_pattern: _ObservedEndpoint.empty() for item in aggregates
    }

    for record in application_events:
        value = record.value
        if not isinstance(value, Mapping):
            raise BaselineFormatError(
                f"{record.source}: application event must be an object"
            )
        try:
            path = normalize_origin_relative_path(value.get("path"))
            method = normalize_method(value.get("method"))
        except ValueError as exc:
            raise BaselineFormatError(
                f"{record.source}: invalid application event method/path"
            ) from exc
        query = value.get("query")
        if not isinstance(query, Mapping):
            raise BaselineFormatError(f"{record.source}: query must be an object")
        status = _normalize_status(value.get("status"), source=record.source)
        try:
            match = matcher.match(path)
        except AmbiguousPathError as exc:
            raise BaselineConsistencyError(
                f"ambiguous application event path {path!r} at {record.source}: {exc}"
            ) from exc
        if not match.matched or match.path_pattern is None:
            raise BaselineConsistencyError(
                f"{record.source}: application event path {path!r} is not in endpoint census"
            )

        endpoint = observed[match.path_pattern]
        query_keys = normalize_key_set(query.keys(), redaction)
        endpoint.total_count += 1
        endpoint.method_counts[method] += 1
        endpoint.status_counts[status] += 1
        endpoint.query_keys.update(query_keys)
        endpoint.variants_by_method[method].add(
            EndpointVariant(query_keys=query_keys, status=status)
        )

    families: list[EndpointFamily] = []
    for aggregate in aggregates:
        actual = observed[aggregate.path_pattern]
        expected_methods = dict(aggregate.method_counts)
        expected_statuses = dict(aggregate.status_counts)
        if actual.total_count != aggregate.count:
            raise BaselineConsistencyError(
                f"{aggregate.source}: endpoint count {aggregate.count} != "
                f"application-event count {actual.total_count}"
            )
        if dict(actual.method_counts) != expected_methods:
            raise BaselineConsistencyError(
                f"{aggregate.source}: method census disagrees with application events"
            )
        if dict(actual.status_counts) != expected_statuses:
            raise BaselineConsistencyError(
                f"{aggregate.source}: status census disagrees with application events"
            )
        if tuple(sorted(actual.query_keys)) != aggregate.query_keys:
            raise BaselineConsistencyError(
                f"{aggregate.source}: query-key census disagrees with application events"
            )

        methods = tuple(
            EndpointMethodContract(
                method=method,
                variants=tuple(sorted(actual.variants_by_method[method])),
            )
            for method in sorted(actual.variants_by_method)
        )
        families.append(
            EndpointFamily(path_pattern=aggregate.path_pattern, methods=methods)
        )
    return tuple(families)


def _canonical_form_action(value: object, *, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise BaselineFormatError(f"{source}: form action must be a non-empty string")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise BaselineFormatError(
            f"{source}: form action must be origin-relative and fragment-free"
        )
    try:
        return normalize_origin_relative_path(parsed.path)
    except ValueError as exc:
        raise BaselineFormatError(f"{source}: invalid form action path") from exc


def _compile_forms(
    records: tuple[_SourceRecord, ...],
    redaction: RedactionPolicy,
) -> tuple[FormSignature, ...]:
    signatures: set[FormSignature] = set()
    for record in records:
        value = record.value
        if not isinstance(value, Mapping):
            raise BaselineFormatError(f"{record.source}: form record must be an object")
        try:
            method = normalize_method(value.get("method"))
        except ValueError as exc:
            raise BaselineFormatError(f"{record.source}: invalid form method") from exc
        action_path = _canonical_form_action(value.get("action"), source=record.source)
        fields = value.get("fields")
        if not isinstance(fields, list):
            raise BaselineFormatError(f"{record.source}: fields must be an array")
        names: list[object] = []
        for index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                raise BaselineFormatError(
                    f"{record.source}: fields[{index}] must be an object"
                )
            names.append(field.get("name"))
        signatures.add(
            FormSignature(
                method=method,
                action_path=action_path,
                field_names=normalize_key_set(names, redaction),
            )
        )
    return tuple(sorted(signatures))


def _match_known_endpoint(
    *,
    raw_path: object,
    method: str,
    source: str,
    matcher: PathMatcher,
    methods_by_pattern: Mapping[str, frozenset[str]],
) -> str:
    try:
        path = normalize_origin_relative_path(raw_path)
        match = matcher.match(path)
    except (ValueError, AmbiguousPathError) as exc:
        raise BaselineConsistencyError(
            f"{source}: path {raw_path!r} cannot map uniquely to endpoint census"
        ) from exc
    if not match.matched or match.path_pattern is None:
        raise BaselineConsistencyError(
            f"{source}: path {path!r} is not represented by endpoint census"
        )
    if method not in methods_by_pattern[match.path_pattern]:
        raise BaselineConsistencyError(
            f"{source}: {method} {path} is not observed in endpoint application events"
        )
    return match.path_pattern


def _compile_operations(
    value: Any,
    *,
    source: str,
    redaction: RedactionPolicy,
    matcher: PathMatcher,
    methods_by_pattern: Mapping[str, frozenset[str]],
) -> tuple[OperationSignature, ...]:
    if not isinstance(value, Mapping):
        raise BaselineFormatError(f"{source}: operation index must be an object")
    operations = value.get("operations")
    if not isinstance(operations, list):
        raise BaselineFormatError(f"{source}: operations must be an array")
    signatures: set[OperationSignature] = set()
    for index, item in enumerate(operations):
        item_source = f"{source}#operation-{index}"
        if not isinstance(item, Mapping):
            raise BaselineFormatError(f"{item_source}: operation must be an object")
        method = "POST"
        pattern = _match_known_endpoint(
            raw_path=item.get("path"),
            method=method,
            source=item_source,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
        )
        query_keys = item.get("query_keys")
        body_keys = item.get("body_keys")
        observations = item.get("observations")
        if not isinstance(query_keys, list) or not isinstance(body_keys, list):
            raise BaselineFormatError(
                f"{item_source}: query_keys and body_keys must be arrays"
            )
        if not isinstance(observations, list) or not observations:
            raise BaselineFormatError(f"{item_source}: observations must be non-empty")
        statuses: set[int] = set()
        for observation_index, observation in enumerate(observations):
            if not isinstance(observation, Mapping):
                raise BaselineFormatError(
                    f"{item_source}: observations[{observation_index}] must be an object"
                )
            statuses.add(
                _normalize_status(
                    observation.get("status"),
                    source=f"{item_source}.observations[{observation_index}]",
                )
            )
        if "count" in item:
            declared_count = _require_non_negative_int(
                item.get("count"), source=item_source, field="count"
            )
            if declared_count != len(observations):
                raise BaselineConsistencyError(
                    f"{item_source}: count {declared_count} != observations {len(observations)}"
                )
        signatures.add(
            OperationSignature(
                method=method,
                path_pattern=pattern,
                query_keys=normalize_key_set(query_keys, redaction),
                body_keys=normalize_key_set(body_keys, redaction),
                statuses=tuple(sorted(statuses)),
            )
        )
    return tuple(sorted(signatures))


def _compile_actions(
    value: Any,
    *,
    source: str,
    redaction: RedactionPolicy,
    matcher: PathMatcher,
    methods_by_pattern: Mapping[str, frozenset[str]],
) -> tuple[ActionRequestFamily, ...]:
    if not isinstance(value, Mapping):
        raise BaselineFormatError(f"{source}: action catalog must be an object")
    items = value.get("items")
    if not isinstance(items, list):
        raise BaselineFormatError(f"{source}: items must be an array")
    if "count" in value:
        declared = _require_non_negative_int(value.get("count"), source=source, field="count")
        if declared != len(items):
            raise BaselineConsistencyError(
                f"{source}: count {declared} != items {len(items)}"
            )

    actions: list[ActionRequestFamily] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        item_source = f"{source}#item-{index}"
        if not isinstance(item, Mapping):
            raise BaselineFormatError(f"{item_source}: action must be an object")
        action_id = item.get("id")
        if not isinstance(action_id, str) or not action_id:
            raise BaselineFormatError(f"{item_source}: id must be a non-empty string")
        if action_id in seen_ids:
            raise BaselineConsistencyError(f"{item_source}: duplicate action id {action_id!r}")
        seen_ids.add(action_id)
        if item.get("confidence") != "observed":
            raise BaselineConsistencyError(
                f"{item_source}: only observed actions may enter Runtime Contract v1"
            )
        try:
            method = normalize_method(item.get("method"))
        except ValueError as exc:
            raise BaselineFormatError(f"{item_source}: invalid action method") from exc
        pattern = _match_known_endpoint(
            raw_path=item.get("path"),
            method=method,
            source=item_source,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
        )

        form_fields = item.get("form_fields")
        query_key_sets = item.get("query_key_sets")
        statuses = item.get("statuses")
        if not isinstance(form_fields, list):
            raise BaselineFormatError(f"{item_source}: form_fields must be an array")
        if not isinstance(query_key_sets, list):
            raise BaselineFormatError(f"{item_source}: query_key_sets must be an array")
        field_names: list[object] = []
        for field_index, field in enumerate(form_fields):
            if not isinstance(field, Mapping):
                raise BaselineFormatError(
                    f"{item_source}: form_fields[{field_index}] must be an object"
                )
            field_names.append(field.get("name"))
        normalized_query_sets: set[tuple[str, ...]] = set()
        for query_index, query_set in enumerate(query_key_sets):
            if not isinstance(query_set, list):
                raise BaselineFormatError(
                    f"{item_source}: query_key_sets[{query_index}] must be an array"
                )
            normalized_query_sets.add(normalize_key_set(query_set, redaction))
        status_counts = _normalized_count_mapping(
            statuses, source=item_source, kind="statuses"
        )
        actions.append(
            ActionRequestFamily(
                action_id=action_id,
                method=method,
                path_pattern=pattern,
                field_names=normalize_key_set(field_names, redaction),
                query_key_sets=tuple(sorted(normalized_query_sets)),
                statuses=tuple(status for status, _ in status_counts),
            )
        )
    return tuple(sorted(actions))


def _redaction_semantics(policy: RedactionPolicy) -> dict[str, Any]:
    return {
        "drop_headers": sorted(name.casefold() for name in policy.drop_headers),
        "drop_field_patterns": sorted(
            {"pattern": pattern.pattern, "flags": pattern.flags}
            for pattern in policy.drop_field_patterns
        , key=lambda item: (item["pattern"], item["flags"])),
        "first_party_only": policy.first_party_only,
        "max_request_bytes": policy.max_request_bytes,
        "max_response_bytes": policy.max_response_bytes,
        "mime_allowlist": sorted({value.casefold() for value in policy.mime_allowlist}),
    }


def _contract_semantics(contract: RuntimeContract) -> dict[str, Any]:
    return {
        "contract_schema_version": contract.contract_schema_version,
        "normalization_version": contract.normalization_version,
        "endpoints": [
            {
                "path_pattern": endpoint.path_pattern,
                "methods": [
                    {
                        "method": method.method,
                        "variants": [
                            {
                                "query_keys": list(variant.query_keys),
                                "status": variant.status,
                            }
                            for variant in method.variants
                        ],
                    }
                    for method in endpoint.methods
                ],
            }
            for endpoint in contract.endpoints
        ],
        "forms": [
            {
                "method": form.method,
                "action_path": form.action_path,
                "field_names": list(form.field_names),
            }
            for form in contract.forms
        ],
        "operations": [
            {
                "method": operation.method,
                "path_pattern": operation.path_pattern,
                "query_keys": list(operation.query_keys),
                "body_keys": list(operation.body_keys),
                "statuses": list(operation.statuses),
            }
            for operation in contract.operations
        ],
        "actions": [
            {
                "action_id": action.action_id,
                "method": action.method,
                "path_pattern": action.path_pattern,
                "field_names": list(action.field_names),
                "query_key_sets": [list(keys) for keys in action.query_key_sets],
                "statuses": list(action.statuses),
            }
            for action in contract.actions
        ],
    }


class BaselineCompiler:
    """Compile curated repository evidence into a deterministic Runtime Contract."""

    @classmethod
    def compile(
        cls,
        repo_root: Path,
        redaction: RedactionPolicy,
    ) -> BaselineCompilation:
        root = Path(repo_root).resolve()
        endpoint_records = _load_partitioned_json(
            root, root / "knowledge/http/endpoints/index.json"
        )
        application_events = _load_partitioned_jsonl(
            root, root / "knowledge/http/application-events/index.json"
        )
        form_records = _load_partitioned_json(
            root, root / "knowledge/http/forms/index.json"
        )

        aggregates = _compile_endpoint_aggregates(endpoint_records, redaction)
        endpoints = _compile_endpoint_families(
            aggregates, application_events, redaction
        )
        matcher = PathMatcher(endpoint.path_pattern for endpoint in endpoints)
        methods_by_pattern = {
            endpoint.path_pattern: frozenset(method.method for method in endpoint.methods)
            for endpoint in endpoints
        }
        forms = _compile_forms(form_records, redaction)

        operation_path = root / "knowledge/http/operation-index.json"
        operation_value = _load_json(operation_path, repo_root=root)
        operations = _compile_operations(
            operation_value,
            source=_source_label(root, operation_path),
            redaction=redaction,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
        )

        action_path = root / "knowledge/actions/catalog.json"
        action_value = _load_json(action_path, repo_root=root)
        actions = _compile_actions(
            action_value,
            source=_source_label(root, action_path),
            redaction=redaction,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
        )

        contract = RuntimeContract(
            contract_schema_version=CONTRACT_SCHEMA_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            endpoints=endpoints,
            forms=forms,
            operations=operations,
            actions=actions,
        )
        return BaselineCompilation(
            contract=contract,
            baseline_sha256=canonical_sha256(_contract_semantics(contract)),
            redaction_policy_sha256=canonical_sha256(_redaction_semantics(redaction)),
        )


__all__ = [
    "BaselineCompilation",
    "BaselineCompiler",
    "BaselineConsistencyError",
    "BaselineError",
    "BaselineFormatError",
]
