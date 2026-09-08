from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.bizman_detector import (
    CONTRACT_SCHEMA_VERSION,
    EXTRACTION_VERSION,
    NORMALIZATION_VERSION,
)
from tools.bizman_detector.model import (
    ActionRequestFamily,
    AnalysisProfile,
    EndpointFamily,
    EndpointMethodContract,
    EndpointVariant,
    FormSignature,
    OperationSignature,
    RuleDescriptor,
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
    resolved_root = repo_root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(resolved_root).as_posix()
    except ValueError:
        return str(resolved)


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


def _partition_manifest(repo_root: Path, index_path: Path) -> tuple[Mapping[str, Any], str]:
    value = _load_json(index_path, repo_root=repo_root)
    label = _source_label(repo_root, index_path)
    if not isinstance(value, Mapping):
        raise BaselineFormatError(f"{label}: partition index must be an object")
    return value, label


def _load_partitioned_json(repo_root: Path, index_path: Path) -> tuple[_SourceRecord, ...]:
    index, index_label = _partition_manifest(repo_root, index_path)
    total = _require_non_negative_int(
        index.get("total_records"), source=index_label, field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list) or not parts:
        raise BaselineFormatError(f"{index_label}: parts must be a non-empty array")
    list_key = index.get("record_key", index.get("list_key"))
    if list_key is not None and not isinstance(list_key, str):
        raise BaselineFormatError(f"{index_label}: record/list key must be a string")

    result: list[_SourceRecord] = []
    seen: set[Path] = set()
    expected_offset = 0
    for part_index, descriptor in enumerate(parts):
        if not isinstance(descriptor, Mapping):
            raise BaselineFormatError(
                f"{index_label}: parts[{part_index}] must be an object"
            )
        part_path = _resolve_declared_part(
            index_path.parent,
            descriptor.get("file", descriptor.get("path")),
            source=index_label,
        )
        if part_path in seen:
            raise BaselineFormatError(f"{index_label}: duplicate part {part_path.name!r}")
        seen.add(part_path)
        declared = _require_non_negative_int(
            descriptor.get("records", descriptor.get("count")),
            source=index_label,
            field=f"parts[{part_index}].records",
        )
        if "offset" in descriptor:
            offset = _require_non_negative_int(
                descriptor.get("offset"),
                source=index_label,
                field=f"parts[{part_index}].offset",
            )
            if offset != expected_offset:
                raise BaselineFormatError(
                    f"{index_label}: {part_path.name} offset {offset} != {expected_offset}"
                )

        payload = _load_json(part_path, repo_root=repo_root)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, Mapping):
            key = list_key or "records"
            records = payload.get(key)
            if not isinstance(records, list):
                raise BaselineFormatError(
                    f"{_source_label(repo_root, part_path)}: {key!r} must be an array"
                )
        else:
            raise BaselineFormatError(
                f"{_source_label(repo_root, part_path)}: partition must be an array or object"
            )
        if len(records) != declared:
            raise BaselineFormatError(
                f"{_source_label(repo_root, part_path)} declares {declared} records "
                f"but contains {len(records)}"
            )
        for record_index, record in enumerate(records):
            result.append(
                _SourceRecord(
                    value=record,
                    source=f"{_source_label(repo_root, part_path)}#record-{record_index}",
                )
            )
        expected_offset += declared

    if expected_offset != total:
        raise BaselineFormatError(
            f"{index_label}: parts contain {expected_offset} records, total_records is {total}"
        )
    return tuple(result)


def _load_partitioned_jsonl(repo_root: Path, index_path: Path) -> tuple[_SourceRecord, ...]:
    index, index_label = _partition_manifest(repo_root, index_path)
    total = _require_non_negative_int(
        index.get("total_records"), source=index_label, field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list) or not parts:
        raise BaselineFormatError(f"{index_label}: parts must be a non-empty array")

    result: list[_SourceRecord] = []
    seen: set[Path] = set()
    expected_offset = 0
    for part_index, descriptor in enumerate(parts):
        if not isinstance(descriptor, Mapping):
            raise BaselineFormatError(
                f"{index_label}: parts[{part_index}] must be an object"
            )
        part_path = _resolve_declared_part(
            index_path.parent,
            descriptor.get("file", descriptor.get("path")),
            source=index_label,
        )
        if part_path in seen:
            raise BaselineFormatError(f"{index_label}: duplicate part {part_path.name!r}")
        seen.add(part_path)
        declared = _require_non_negative_int(
            descriptor.get("records", descriptor.get("count")),
            source=index_label,
            field=f"parts[{part_index}].records",
        )
        if "offset" in descriptor:
            offset = _require_non_negative_int(
                descriptor.get("offset"),
                source=index_label,
                field=f"parts[{part_index}].offset",
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
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise BaselineFormatError(
                            f"invalid JSONL in {label}:{line_number}: {exc}"
                        ) from exc
                    result.append(_SourceRecord(record, f"{label}:{line_number}"))
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
    return tuple(result)


def _normalize_status(value: object, *, source: str) -> int:
    if isinstance(value, bool):
        raise BaselineFormatError(f"{source}: status must be an integer in 100..599")
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 100 <= value <= 599:
        raise BaselineFormatError(f"{source}: status must be an integer in 100..599")
    return value


def _count_map(value: object, *, source: str, kind: str) -> tuple[tuple[Any, int], ...]:
    if not isinstance(value, Mapping) or not value:
        raise BaselineFormatError(f"{source}: {kind} must be a non-empty object")
    normalized: dict[Any, int] = {}
    for raw_key, raw_count in value.items():
        if kind == "methods":
            try:
                key: Any = normalize_method(raw_key)
            except ValueError as exc:
                raise BaselineFormatError(f"{source}: invalid method {raw_key!r}") from exc
        else:
            key = _normalize_status(raw_key, source=source)
        count = _require_non_negative_int(
            raw_count, source=source, field=f"{kind}.{raw_key}"
        )
        if count == 0:
            raise BaselineFormatError(f"{source}: {kind}.{raw_key} count must be positive")
        if key in normalized:
            raise BaselineFormatError(f"{source}: duplicate normalized {kind} key {key!r}")
        normalized[key] = count
    return tuple(sorted(normalized.items()))


def _endpoint_aggregates(
    records: tuple[_SourceRecord, ...], redaction: RedactionPolicy
) -> tuple[_EndpointAggregate, ...]:
    result: list[_EndpointAggregate] = []
    seen: set[str] = set()
    for record in records:
        value = record.value
        if not isinstance(value, Mapping):
            raise BaselineFormatError(f"{record.source}: endpoint record must be an object")
        try:
            pattern = normalize_origin_relative_path(value.get("path_pattern"))
        except ValueError as exc:
            raise BaselineFormatError(f"{record.source}: invalid path_pattern") from exc
        if pattern in seen:
            raise BaselineConsistencyError(
                f"{record.source}: duplicate endpoint path_pattern {pattern!r}"
            )
        seen.add(pattern)
        count = _require_non_negative_int(value.get("count"), source=record.source, field="count")
        if count == 0:
            raise BaselineFormatError(f"{record.source}: endpoint count must be positive")
        methods = _count_map(value.get("methods"), source=record.source, kind="methods")
        statuses = _count_map(value.get("statuses"), source=record.source, kind="statuses")
        query_keys = value.get("query_keys")
        if not isinstance(query_keys, list):
            raise BaselineFormatError(f"{record.source}: query_keys must be an array")
        if sum(count_value for _, count_value in methods) != count:
            raise BaselineConsistencyError(
                f"{record.source}: method counts do not sum to endpoint count {count}"
            )
        if sum(count_value for _, count_value in statuses) != count:
            raise BaselineConsistencyError(
                f"{record.source}: status counts do not sum to endpoint count {count}"
            )
        result.append(
            _EndpointAggregate(
                path_pattern=pattern,
                count=count,
                method_counts=methods,
                status_counts=statuses,
                query_keys=normalize_key_set(query_keys, redaction),
                source=record.source,
            )
        )
    return tuple(sorted(result, key=lambda item: item.path_pattern))


def _endpoint_families(
    aggregates: tuple[_EndpointAggregate, ...],
    events: tuple[_SourceRecord, ...],
    redaction: RedactionPolicy,
) -> tuple[EndpointFamily, ...]:
    matcher = PathMatcher(item.path_pattern for item in aggregates)
    observed = {item.path_pattern: _ObservedEndpoint.empty() for item in aggregates}

    for record in events:
        value = record.value
        if not isinstance(value, Mapping):
            raise BaselineFormatError(f"{record.source}: application event must be an object")
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
        query_keys = normalize_key_set(query.keys(), redaction)
        target = observed[match.path_pattern]
        target.total_count += 1
        target.method_counts[method] += 1
        target.status_counts[status] += 1
        target.query_keys.update(query_keys)
        target.variants_by_method[method].add(
            EndpointVariant(query_keys=query_keys, status=status)
        )

    families: list[EndpointFamily] = []
    for aggregate in aggregates:
        actual = observed[aggregate.path_pattern]
        if actual.total_count != aggregate.count:
            raise BaselineConsistencyError(
                f"{aggregate.source}: endpoint count {aggregate.count} != "
                f"application-event count {actual.total_count}"
            )
        if dict(actual.method_counts) != dict(aggregate.method_counts):
            raise BaselineConsistencyError(
                f"{aggregate.source}: method census disagrees with application events"
            )
        if dict(actual.status_counts) != dict(aggregate.status_counts):
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
        families.append(EndpointFamily(aggregate.path_pattern, methods))
    return tuple(families)


def _form_action_path(value: object, *, source: str, matcher: PathMatcher) -> str:
    if not isinstance(value, str) or not value:
        raise BaselineFormatError(f"{source}: form action must be a non-empty string")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise BaselineFormatError(
            f"{source}: form action must be origin-relative and fragment-free"
        )
    try:
        path = normalize_origin_relative_path(parsed.path)
    except ValueError as exc:
        raise BaselineFormatError(f"{source}: invalid form action path") from exc
    try:
        match = matcher.match(path)
    except AmbiguousPathError as exc:
        raise BaselineConsistencyError(
            f"{source}: form action path {path!r} is ambiguous in endpoint census"
        ) from exc
    if match.matched and match.path_pattern is not None:
        return match.path_pattern
    return path


def _forms(
    records: tuple[_SourceRecord, ...],
    redaction: RedactionPolicy,
    matcher: PathMatcher,
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
                action_path=_form_action_path(
                    value.get("action"), source=record.source, matcher=matcher
                ),
                field_names=normalize_key_set(names, redaction),
            )
        )
    return tuple(sorted(signatures))


def _known_endpoint(
    raw_path: object,
    *,
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
            f"{source}: path {path!r} is not represented by Runtime Contract endpoints"
        )
    if method not in methods_by_pattern[match.path_pattern]:
        raise BaselineConsistencyError(
            f"{source}: {method} {path} is not represented by Runtime Contract endpoints"
        )
    return match.path_pattern


def _operation_path(raw_path: object, *, source: str, matcher: PathMatcher) -> str:
    try:
        path = normalize_origin_relative_path(raw_path)
    except ValueError as exc:
        raise BaselineFormatError(f"{source}: invalid operation path") from exc
    try:
        match = matcher.match(path)
    except AmbiguousPathError as exc:
        raise BaselineConsistencyError(
            f"{source}: operation path {path!r} is ambiguous in endpoint census"
        ) from exc
    if match.matched and match.path_pattern is not None:
        return match.path_pattern
    return path


def _operations(
    value: Any,
    *,
    source: str,
    redaction: RedactionPolicy,
    matcher: PathMatcher,
) -> tuple[OperationSignature, ...]:
    if not isinstance(value, Mapping) or not isinstance(value.get("operations"), list):
        raise BaselineFormatError(f"{source}: operations must be an array")
    items = value["operations"]
    signatures: set[OperationSignature] = set()
    observed_total = 0
    for index, item in enumerate(items):
        item_source = f"{source}#operation-{index}"
        if not isinstance(item, Mapping):
            raise BaselineFormatError(f"{item_source}: operation must be an object")
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
        declared_count = len(observations)
        if "count" in item:
            declared_count = _require_non_negative_int(
                item.get("count"), source=item_source, field="count"
            )
            if declared_count != len(observations):
                raise BaselineConsistencyError(
                    f"{item_source}: count {declared_count} != observations {len(observations)}"
                )
        observed_total += declared_count
        signatures.add(
            OperationSignature(
                method="POST",
                path_pattern=_operation_path(
                    item.get("path"),
                    source=item_source,
                    matcher=matcher,
                ),
                query_keys=normalize_key_set(query_keys, redaction),
                body_keys=normalize_key_set(body_keys, redaction),
                statuses=tuple(sorted(statuses)),
            )
        )
    if "post_count" in value:
        post_count = _require_non_negative_int(
            value.get("post_count"), source=source, field="post_count"
        )
        if post_count != observed_total:
            raise BaselineConsistencyError(
                f"{source}: post_count {post_count} != operation observations {observed_total}"
            )
    return tuple(sorted(signatures))


def _merge_operation_endpoints(
    endpoints: tuple[EndpointFamily, ...],
    operations: tuple[OperationSignature, ...],
) -> tuple[EndpointFamily, ...]:
    variants_by_path: dict[str, dict[str, set[EndpointVariant]]] = {
        endpoint.path_pattern: {
            method.method: set(method.variants) for method in endpoint.methods
        }
        for endpoint in endpoints
    }
    for operation in operations:
        methods = variants_by_path.setdefault(operation.path_pattern, {})
        variants = methods.setdefault(operation.method, set())
        variants.update(
            EndpointVariant(query_keys=operation.query_keys, status=status)
            for status in operation.statuses
        )

    return tuple(
        EndpointFamily(
            path_pattern=path_pattern,
            methods=tuple(
                EndpointMethodContract(
                    method=method,
                    variants=tuple(sorted(variants)),
                )
                for method, variants in sorted(methods.items())
            ),
        )
        for path_pattern, methods in sorted(variants_by_path.items())
    )


def _actions(
    value: Any,
    *,
    source: str,
    redaction: RedactionPolicy,
    matcher: PathMatcher,
    methods_by_pattern: Mapping[str, frozenset[str]],
    operations: tuple[OperationSignature, ...],
) -> tuple[ActionRequestFamily, ...]:
    if not isinstance(value, Mapping) or not isinstance(value.get("items"), list):
        raise BaselineFormatError(f"{source}: items must be an array")
    items = value["items"]
    if "count" in value:
        declared = _require_non_negative_int(value.get("count"), source=source, field="count")
        if declared != len(items):
            raise BaselineConsistencyError(f"{source}: count {declared} != items {len(items)}")

    result: list[ActionRequestFamily] = []
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
        form_fields = item.get("form_fields")
        query_sets = item.get("query_key_sets")
        if not isinstance(form_fields, list) or not isinstance(query_sets, list):
            raise BaselineFormatError(
                f"{item_source}: form_fields and query_key_sets must be arrays"
            )
        field_names: list[object] = []
        for field_index, field in enumerate(form_fields):
            if not isinstance(field, Mapping):
                raise BaselineFormatError(
                    f"{item_source}: form_fields[{field_index}] must be an object"
                )
            field_names.append(field.get("name"))
        normalized_field_names = normalize_key_set(field_names, redaction)
        normalized_query_sets: set[tuple[str, ...]] = set()
        for query_index, query_keys in enumerate(query_sets):
            if not isinstance(query_keys, list):
                raise BaselineFormatError(
                    f"{item_source}: query_key_sets[{query_index}] must be an array"
                )
            normalized_query_sets.add(normalize_key_set(query_keys, redaction))
        status_counts = _count_map(
            item.get("statuses"), source=item_source, kind="statuses"
        )
        if "observed_count" in item:
            observed_count = _require_non_negative_int(
                item.get("observed_count"), source=item_source, field="observed_count"
            )
            if observed_count != sum(count for _, count in status_counts):
                raise BaselineConsistencyError(
                    f"{item_source}: observed_count disagrees with status counts"
                )
        path_pattern = _known_endpoint(
            item.get("path"),
            method=method,
            source=item_source,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
        )
        matching_operations = tuple(
            operation
            for operation in operations
            if operation.method == method and operation.path_pattern == path_pattern
        )
        if not matching_operations:
            raise BaselineConsistencyError(
                f"{item_source}: action has no matching operation contract"
            )
        operation_query_sets = {operation.query_keys for operation in matching_operations}
        unsupported_query_sets = normalized_query_sets - operation_query_sets
        if unsupported_query_sets:
            raise BaselineConsistencyError(
                f"{item_source}: action query shape is not represented by operation contracts"
            )
        operation_body_fields = {
            key for operation in matching_operations for key in operation.body_keys
        }
        unsupported_fields = set(normalized_field_names) - operation_body_fields
        if unsupported_fields:
            raise BaselineConsistencyError(
                f"{item_source}: action fields are not represented by operation contracts"
            )
        statuses = tuple(status for status, _ in status_counts)
        operation_statuses = {
            status for operation in matching_operations for status in operation.statuses
        }
        if set(statuses) - operation_statuses:
            raise BaselineConsistencyError(
                f"{item_source}: action statuses are not represented by operation contracts"
            )
        result.append(
            ActionRequestFamily(
                action_id=action_id,
                method=method,
                path_pattern=path_pattern,
                field_names=normalized_field_names,
                query_key_sets=tuple(sorted(normalized_query_sets)),
                statuses=statuses,
            )
        )
    return tuple(sorted(result))


def _redaction_semantics(policy: RedactionPolicy) -> dict[str, Any]:
    patterns = [
        {"pattern": pattern.pattern, "flags": pattern.flags}
        for pattern in policy.drop_field_patterns
    ]
    patterns.sort(key=lambda item: (item["pattern"], item["flags"]))
    return {
        "drop_headers": sorted(name.casefold() for name in policy.drop_headers),
        "drop_field_patterns": patterns,
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


def _require_positive_version(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def build_analysis_profile(
    compilation: BaselineCompilation,
    rules: Iterable[RuleDescriptor],
    *,
    extraction_version: int = EXTRACTION_VERSION,
) -> AnalysisProfile:
    """Build the deterministic checkpoint namespace for detector interpretation."""

    contract_schema_version = _require_positive_version(
        compilation.contract.contract_schema_version,
        name="contract_schema_version",
    )
    normalization_version = _require_positive_version(
        compilation.contract.normalization_version,
        name="normalization_version",
    )
    extraction = _require_positive_version(
        extraction_version,
        name="extraction_version",
    )
    baseline_sha256 = _require_sha256(
        compilation.baseline_sha256,
        name="baseline_sha256",
    )
    redaction_policy_sha256 = _require_sha256(
        compilation.redaction_policy_sha256,
        name="redaction_policy_sha256",
    )

    descriptors = tuple(rules)
    seen_ids: set[str] = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, RuleDescriptor):
            raise TypeError("rules must contain RuleDescriptor values")
        if not descriptor.rule_id:
            raise ValueError("rule id must be non-empty")
        if descriptor.rule_id in seen_ids:
            raise ValueError(f"duplicate rule id: {descriptor.rule_id}")
        seen_ids.add(descriptor.rule_id)
        _require_positive_version(descriptor.version, name="rule version")
        if not descriptor.kind:
            raise ValueError("rule kind must be non-empty")

    ordered_rules = tuple(sorted(descriptors, key=lambda item: item.rule_id))
    semantics = {
        "baseline_sha256": baseline_sha256,
        "contract_schema_version": contract_schema_version,
        "normalization_version": normalization_version,
        "extraction_version": extraction,
        "redaction_policy_sha256": redaction_policy_sha256,
        "rules": [
            {"rule_id": descriptor.rule_id, "rule_version": descriptor.version}
            for descriptor in ordered_rules
        ],
    }
    return AnalysisProfile(
        baseline_sha256=baseline_sha256,
        contract_schema_version=contract_schema_version,
        normalization_version=normalization_version,
        extraction_version=extraction,
        redaction_policy_sha256=redaction_policy_sha256,
        rules=ordered_rules,
        sha256=canonical_sha256(semantics),
    )


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

        aggregates = _endpoint_aggregates(endpoint_records, redaction)
        endpoints = _endpoint_families(aggregates, application_events, redaction)
        matcher = PathMatcher(endpoint.path_pattern for endpoint in endpoints)
        forms = _forms(form_records, redaction, matcher)

        operation_path = root / "knowledge/http/operation-index.json"
        operations = _operations(
            _load_json(operation_path, repo_root=root),
            source=_source_label(root, operation_path),
            redaction=redaction,
            matcher=matcher,
        )
        endpoints = _merge_operation_endpoints(endpoints, operations)
        matcher = PathMatcher(endpoint.path_pattern for endpoint in endpoints)
        methods_by_pattern = {
            endpoint.path_pattern: frozenset(method.method for method in endpoint.methods)
            for endpoint in endpoints
        }

        action_path = root / "knowledge/actions/catalog.json"
        actions = _actions(
            _load_json(action_path, repo_root=root),
            source=_source_label(root, action_path),
            redaction=redaction,
            matcher=matcher,
            methods_by_pattern=methods_by_pattern,
            operations=operations,
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
    "build_analysis_profile",
]
