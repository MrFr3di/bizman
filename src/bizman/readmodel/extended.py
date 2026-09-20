from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.model import KnowledgeRecord, RefKind
from bizman.readmodel.projection_support import (
    load_json as _load_json,
    non_negative_int as _non_negative_int,
    require_mapping as _mapping,
    require_string as _text,
    safe_child as _safe_child,
    string_list as _string_list,
)


_FORM_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_OPERATION_ID_RE = re.compile(r"^op-[0-9]{3}$")


def _versioned_ref(namespace: str, semantic_identity: Mapping[str, object]) -> str:
    digest = canonical_sha256(dict(semantic_identity))[:24]
    return f"bm.{namespace}.v1.{digest}"


def _capture_sources(root: Path) -> dict[str, str]:
    path = root / "knowledge" / "sources" / "captures.json"
    document = _mapping(_load_json(path), source=path.as_posix())
    captures = document.get("captures")
    if not isinstance(captures, list):
        raise ValueError(f"{path}: captures must be an array")

    result: dict[str, str] = {}
    seen_source_ids: set[str] = set()
    for index, raw in enumerate(captures):
        source = f"{path.as_posix()}#capture-{index}"
        item = _mapping(raw, source=source)
        file_name = _text(item.get("file_name"), source=source, field="file_name")
        source_id = _text(item.get("source_id"), source=source, field="source_id")
        if file_name in result:
            raise ValueError(f"{source}: duplicate capture file_name {file_name!r}")
        if source_id in seen_source_ids:
            raise ValueError(f"{source}: duplicate capture source_id {source_id!r}")
        result[file_name] = source_id
        seen_source_ids.add(source_id)
    return result


def _observation_ref(
    value: object,
    *,
    capture_sources: Mapping[str, str],
    source: str,
) -> str:
    item = _mapping(value, source=source)
    if "capture" in item:
        capture = _text(item.get("capture"), source=source, field="capture")
        try:
            source_id = capture_sources[capture]
        except KeyError as exc:
            raise ValueError(f"{source}: unknown capture {capture!r}") from exc
        entry = _non_negative_int(item.get("entry"), source=source, field="entry")
        return f"{source_id}#entry-{entry}"

    if "session" in item:
        session = _text(item.get("session"), source=source, field="session")
        sequence = _non_negative_int(item.get("seq"), source=source, field="seq")
        return f"{session}#seq-{sequence}"

    raise ValueError(f"{source}: observation requires capture/entry or session/seq")


def _endpoint_records(
    root: Path,
    *,
    capture_sources: Mapping[str, str],
) -> list[KnowledgeRecord]:
    dataset_root = root / "knowledge" / "http" / "endpoints"
    index_path = dataset_root / "index.json"
    index = _mapping(_load_json(index_path), source=index_path.as_posix())
    if index.get("schema_version") != "1.0":
        raise ValueError(f"{index_path}: unsupported schema_version")
    total = _non_negative_int(
        index.get("total_records"), source=index_path.as_posix(), field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list):
        raise ValueError(f"{index_path}: parts must be an array")

    records: list[KnowledgeRecord] = []
    seen_parts: set[Path] = set()
    seen_paths: set[str] = set()
    for part_index, raw_part in enumerate(parts):
        source = f"{index_path.as_posix()}#part-{part_index}"
        part = _mapping(raw_part, source=source)
        declared = _non_negative_int(part.get("records"), source=source, field="records")
        part_path = _safe_child(dataset_root, part.get("file"), source=source, field="part file")
        if part_path in seen_parts:
            raise ValueError(f"{source}: duplicate endpoint part {part_path.name!r}")
        seen_parts.add(part_path)

        document = _mapping(_load_json(part_path), source=part_path.as_posix())
        if document.get("schema_version") != "1.0":
            raise ValueError(f"{part_path}: unsupported schema_version")
        items = document.get("records")
        if not isinstance(items, list):
            raise ValueError(f"{part_path}: records must be an array")
        if len(items) != declared:
            raise ValueError(
                f"{part_path}: declared {declared} records but contains {len(items)}"
            )

        for item_index, raw in enumerate(items):
            item_source = f"{part_path.as_posix()}#record-{item_index}"
            item = _mapping(raw, source=item_source)
            path_pattern = _text(
                item.get("path_pattern"), source=item_source, field="path_pattern"
            )
            if path_pattern in seen_paths:
                raise ValueError(f"{item_source}: duplicate endpoint path_pattern")
            seen_paths.add(path_pattern)

            methods_raw = item.get("methods")
            if not isinstance(methods_raw, Mapping) or not methods_raw:
                raise ValueError(f"{item_source}: methods must be a non-empty object")
            methods = tuple(
                sorted(
                    _text(method, source=item_source, field="method").upper()
                    for method in methods_raw
                )
            )
            query_keys = tuple(
                sorted(
                    _string_list(
                        item.get("query_keys", []),
                        source=item_source,
                        field="query_keys",
                    )
                )
            )
            mime_types = item.get("mime_types", {})
            resource_types = item.get("resource_types", {})
            if not isinstance(mime_types, Mapping) or not isinstance(resource_types, Mapping):
                raise ValueError(
                    f"{item_source}: mime_types/resource_types must be objects"
                )

            examples = item.get("examples")
            if not isinstance(examples, list) or not examples:
                raise ValueError(f"{item_source}: examples must be a non-empty array")
            evidence = tuple(
                _observation_ref(
                    example,
                    capture_sources=capture_sources,
                    source=f"{item_source}.examples[{example_index}]",
                )
                for example_index, example in enumerate(examples)
            )

            ref = _versioned_ref("endpoint", {"path_pattern": path_pattern})
            aliases = (
                f"endpoint:{path_pattern}",
                path_pattern,
                *(f"{method} {path_pattern}" for method in methods),
            )
            body = " ".join(
                (
                    path_pattern,
                    *methods,
                    *query_keys,
                    *sorted(str(key) for key in mime_types),
                    *sorted(str(key) for key in resource_types),
                )
            )
            records.append(
                KnowledgeRecord(
                    ref=ref,
                    kind=RefKind.ENDPOINT,
                    title=path_pattern,
                    aliases=aliases,
                    body=body,
                    evidence_refs=evidence,
                    source_dataset="endpoints",
                )
            )

    if len(records) != total:
        raise ValueError(
            f"{index_path}: declared total_records {total} != {len(records)} records"
        )
    return records


def _operation_records(
    root: Path,
    *,
    capture_sources: Mapping[str, str],
) -> list[KnowledgeRecord]:
    path = root / "knowledge" / "http" / "operation-index.json"
    document = _mapping(_load_json(path), source=path.as_posix())
    if document.get("schema_version") != "1.0":
        raise ValueError(f"{path}: unsupported schema_version")
    post_count = _non_negative_int(
        document.get("post_count"), source=path.as_posix(), field="post_count"
    )
    items = document.get("operations")
    if not isinstance(items, list):
        raise ValueError(f"{path}: operations must be an array")

    records: list[KnowledgeRecord] = []
    seen_ids: set[str] = set()
    observed_total = 0
    for index, raw in enumerate(items):
        source = f"{path.as_posix()}#operation-{index}"
        item = _mapping(raw, source=source)
        operation_id = _text(item.get("id"), source=source, field="id")
        if _OPERATION_ID_RE.fullmatch(operation_id) is None:
            raise ValueError(f"{source}: invalid operation id {operation_id!r}")
        if operation_id in seen_ids:
            raise ValueError(f"{source}: duplicate operation id {operation_id!r}")
        seen_ids.add(operation_id)

        route = _text(item.get("path"), source=source, field="path")
        query_keys = tuple(
            sorted(_string_list(item.get("query_keys"), source=source, field="query_keys"))
        )
        body_keys = tuple(
            sorted(_string_list(item.get("body_keys"), source=source, field="body_keys"))
        )
        declared_count = _non_negative_int(
            item.get("count"), source=source, field="count"
        )
        observations = item.get("observations")
        if not isinstance(observations, list) or len(observations) != declared_count:
            raise ValueError(
                f"{source}: observations must match declared count {declared_count}"
            )
        evidence = tuple(
            _observation_ref(
                observation,
                capture_sources=capture_sources,
                source=f"{source}.observations[{obs_index}]",
            )
            for obs_index, observation in enumerate(observations)
        )
        observed_total += declared_count

        semantic_identity = {
            "path": route,
            "query_keys": list(query_keys),
            "body_keys": list(body_keys),
        }
        ref = _versioned_ref("operation", semantic_identity)
        aliases = (
            operation_id,
            f"operation:{operation_id}",
            f"POST {route}",
        )
        body = " ".join(("POST", route, *query_keys, *body_keys))
        records.append(
            KnowledgeRecord(
                ref=ref,
                kind=RefKind.OPERATION,
                title=f"POST {route} [{operation_id}]",
                aliases=aliases,
                body=body,
                evidence_refs=evidence,
                source_dataset="operations",
            )
        )

    if observed_total != post_count:
        raise ValueError(
            f"{path}: post_count {post_count} != summed operation count {observed_total}"
        )
    return records


def _relative_action(value: object, *, source: str) -> tuple[str, tuple[str, ...]]:
    raw = _text(value, source=source, field="action")
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"{source}: form action must be first-party relative")
    path = parsed.path or "/"
    query_keys = tuple(sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}))
    return path, query_keys


def _form_records(
    root: Path,
    *,
    capture_sources: Mapping[str, str],
) -> list[KnowledgeRecord]:
    dataset_root = root / "knowledge" / "http" / "forms"
    index_path = dataset_root / "index.json"
    index = _mapping(_load_json(index_path), source=index_path.as_posix())
    if index.get("schema_version") != "1.0":
        raise ValueError(f"{index_path}: unsupported schema_version")
    total = _non_negative_int(
        index.get("total_records"), source=index_path.as_posix(), field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list):
        raise ValueError(f"{index_path}: parts must be an array")

    records: list[KnowledgeRecord] = []
    seen_ids: set[str] = set()
    seen_parts: set[Path] = set()
    expected_offset = 0
    for part_index, raw_part in enumerate(parts):
        source = f"{index_path.as_posix()}#part-{part_index}"
        part = _mapping(raw_part, source=source)
        declared = _non_negative_int(part.get("records"), source=source, field="records")
        offset = _non_negative_int(part.get("offset"), source=source, field="offset")
        if offset != expected_offset:
            raise ValueError(f"{source}: offset {offset} != expected {expected_offset}")

        part_path = _safe_child(dataset_root, part.get("file"), source=source, field="part file")
        if part_path in seen_parts:
            raise ValueError(f"{source}: duplicate form part {part_path.name!r}")
        seen_parts.add(part_path)
        document = _load_json(part_path)
        if not isinstance(document, list) or len(document) != declared:
            raise ValueError(
                f"{part_path}: form array must contain declared {declared} records"
            )

        for item_index, raw in enumerate(document):
            item_source = f"{part_path.as_posix()}#form-{item_index}"
            item = _mapping(raw, source=item_source)
            form_id = _text(item.get("form_id"), source=item_source, field="form_id")
            if _FORM_ID_RE.fullmatch(form_id) is None:
                raise ValueError(f"{item_source}: invalid form_id {form_id!r}")
            if form_id in seen_ids:
                raise ValueError(f"{item_source}: duplicate form_id {form_id!r}")
            seen_ids.add(form_id)

            method = _text(item.get("method"), source=item_source, field="method").upper()
            if method not in {"GET", "POST"}:
                raise ValueError(f"{item_source}: unsupported form method {method!r}")
            action_path, action_query_keys = _relative_action(
                item.get("action"), source=item_source
            )

            fields = item.get("fields")
            if not isinstance(fields, list):
                raise ValueError(f"{item_source}: fields must be an array")
            field_terms: list[str] = []
            for field_index, raw_field in enumerate(fields):
                field_source = f"{item_source}.fields[{field_index}]"
                field = _mapping(raw_field, source=field_source)
                field_terms.append(
                    _text(field.get("name"), source=field_source, field="name")
                )
                field_type = field.get("type")
                if field_type is not None:
                    field_terms.append(
                        _text(field_type, source=field_source, field="type")
                    )

            observed_on = item.get("observed_on")
            if not isinstance(observed_on, list) or not observed_on:
                raise ValueError(f"{item_source}: observed_on must be a non-empty array")
            evidence = tuple(
                _observation_ref(
                    observation,
                    capture_sources=capture_sources,
                    source=f"{item_source}.observed_on[{obs_index}]",
                )
                for obs_index, observation in enumerate(observed_on)
            )

            ref = f"bm.form.v1.{form_id}"
            aliases = (
                form_id,
                f"form:{form_id}",
                f"{method} {action_path}",
            )
            body = " ".join(
                (method, action_path, *action_query_keys, *sorted(field_terms))
            )
            records.append(
                KnowledgeRecord(
                    ref=ref,
                    kind=RefKind.FORM,
                    title=f"{method} {action_path}",
                    aliases=aliases,
                    body=body,
                    evidence_refs=evidence,
                    source_dataset="forms",
                )
            )
        expected_offset += declared

    if len(records) != total:
        raise ValueError(
            f"{index_path}: declared total_records {total} != {len(records)} records"
        )
    return records


def _load_jsonl(path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot open curated knowledge file {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            records.append(_mapping(value, source=f"{path}:{line_number}"))
    return records


def _wiki_records(
    root: Path,
    *,
    capture_sources: Mapping[str, str],
) -> list[KnowledgeRecord]:
    dataset_root = root / "knowledge" / "wiki" / "topics"
    index_path = dataset_root / "index.json"
    index = _mapping(_load_json(index_path), source=index_path.as_posix())
    if index.get("schema_version") != "1.0":
        raise ValueError(f"{index_path}: unsupported schema_version")
    total = _non_negative_int(
        index.get("total_records"), source=index_path.as_posix(), field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list):
        raise ValueError(f"{index_path}: parts must be an array")

    records: list[KnowledgeRecord] = []
    seen_topics: set[str] = set()
    seen_parts: set[Path] = set()
    for part_index, raw_part in enumerate(parts):
        source = f"{index_path.as_posix()}#part-{part_index}"
        part = _mapping(raw_part, source=source)
        declared = _non_negative_int(part.get("records"), source=source, field="records")
        part_path = _safe_child(dataset_root, part.get("file"), source=source, field="part file")
        if part_path in seen_parts:
            raise ValueError(f"{source}: duplicate Wiki part {part_path.name!r}")
        seen_parts.add(part_path)

        items = _load_jsonl(part_path)
        if len(items) != declared:
            raise ValueError(
                f"{part_path}: declared {declared} records but contains {len(items)}"
            )
        for item_index, item in enumerate(items):
            item_source = f"{part_path.as_posix()}#topic-{item_index}"
            topic = _text(item.get("topic"), source=item_source, field="topic")
            if topic in seen_topics:
                raise ValueError(f"{item_source}: duplicate Wiki topic {topic!r}")
            seen_topics.add(topic)
            text_value = item.get("text")
            if not isinstance(text_value, str):
                raise ValueError(f"{item_source}: text must be a string")
            text = text_value
            related = _string_list(
                item.get("related_topics", []),
                source=item_source,
                field="related_topics",
            )
            source_record = _mapping(item.get("source"), source=f"{item_source}.source")
            evidence = (
                _observation_ref(
                    source_record,
                    capture_sources=capture_sources,
                    source=f"{item_source}.source",
                ),
            )

            ref = _versioned_ref("wiki", {"topic": topic})
            records.append(
                KnowledgeRecord(
                    ref=ref,
                    kind=RefKind.WIKI_TOPIC,
                    title=topic,
                    aliases=(topic, f"wiki:{topic}"),
                    body=" ".join((topic, text, *related)),
                    evidence_refs=evidence,
                    source_dataset="wiki_topics",
                )
            )

    if len(records) != total:
        raise ValueError(
            f"{index_path}: declared total_records {total} != {len(records)} records"
        )
    return records


def extended_knowledge_records(root: Path) -> tuple[KnowledgeRecord, ...]:
    capture_sources = _capture_sources(root)
    return tuple(
        (
            *_endpoint_records(root, capture_sources=capture_sources),
            *_operation_records(root, capture_sources=capture_sources),
            *_form_records(root, capture_sources=capture_sources),
            *_wiki_records(root, capture_sources=capture_sources),
        )
    )


__all__ = ["extended_knowledge_records"]