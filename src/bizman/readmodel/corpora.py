from __future__ import annotations

import json
from pathlib import Path
import re
from collections.abc import Callable
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.curated_io import (
    load_json as _load_json,
    non_negative_int as _non_negative_int,
    require_mapping as _require_mapping,
    require_string as _require_string,
    safe_child as _safe_part,
    string_list as _string_list,
)
from bizman.readmodel.model import KnowledgeRecord, RefKind


_FORM_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID_RE = re.compile(r"^src\.[a-z0-9][a-z0-9.-]*$")



def _load_jsonl(path: Path) -> list[tuple[Mapping[str, Any], str]]:
    rows: list[tuple[Mapping[str, Any], str]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise ValueError(f"{path}:{line_number}: blank JSONL line")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path}:{line_number}: invalid JSON: {exc.msg}"
                    ) from exc
                rows.append(
                    (_require_mapping(value, source=f"{path}:{line_number}"), f"{path}:{line_number}")
                )
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot load curated knowledge file {path}: {exc}") from exc
    return rows



def _capture_source_map(repo_root: Path) -> dict[str, str]:
    path = repo_root / "knowledge" / "sources" / "captures.json"
    document = _require_mapping(_load_json(path), source=path.as_posix())
    if document.get("schema_version") != "2.0":
        raise ValueError(f"{path}: unsupported captures schema_version")
    captures = document.get("captures")
    if not isinstance(captures, list):
        raise ValueError(f"{path}: captures must be an array")

    result: dict[str, str] = {}
    source_ids: set[str] = set()
    for index, raw in enumerate(captures):
        source = f"{path.as_posix()}#capture-{index}"
        capture = _require_mapping(raw, source=source)
        filename = _require_string(
            capture.get("file_name"), source=source, field="file_name"
        )
        source_id = _require_string(
            capture.get("source_id"), source=source, field="source_id"
        )
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise ValueError(
                f"{source}: source_id must be a canonical src.* identifier"
            )
        sha256 = _require_string(
            capture.get("sha256"), source=source, field="sha256"
        )
        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError(
                f"{source}: sha256 must be 64 lowercase hexadecimal characters"
            )
        _non_negative_int(capture.get("bytes"), source=source, field="bytes")
        _non_negative_int(capture.get("entries"), source=source, field="entries")

        if filename in result:
            raise ValueError(f"{source}: duplicate capture filename {filename!r}")
        if source_id in source_ids:
            raise ValueError(f"{source}: duplicate capture source_id {source_id!r}")
        source_ids.add(source_id)
        result[filename] = source_id
    return result


def _observation_evidence(
    value: object,
    capture_sources: Mapping[str, str],
    *,
    source: str,
) -> str:
    item = _require_mapping(value, source=source)
    capture = item.get("capture")
    if capture is not None:
        filename = _require_string(capture, source=source, field="capture")
        try:
            source_id = capture_sources[filename]
        except KeyError as exc:
            raise ValueError(f"{source}: unknown capture {filename!r}") from exc
        entry = _non_negative_int(item.get("entry"), source=source, field="entry")
        return f"{source_id}#entry-{entry}"

    session = item.get("session")
    if session is not None:
        session_id = _require_string(session, source=source, field="session")
        sequence = _non_negative_int(item.get("seq"), source=source, field="seq")
        return f"{session_id}#seq-{sequence}"

    raise ValueError(f"{source}: observation requires capture/entry or session/seq")


def _versioned_ref(kind: RefKind, semantic_identity: Mapping[str, object]) -> str:
    return f"bm.{kind.value}.v1.{canonical_sha256(dict(semantic_identity))}"


def _partition_rows(
    index_path: Path,
    parts_root: Path,
    *,
    require_offsets: bool,
    load_part: Callable[[Path], list[tuple[Mapping[str, Any], str]]],
) -> list[tuple[Mapping[str, Any], str]]:
    index = _require_mapping(_load_json(index_path), source=index_path.as_posix())
    if index.get("schema_version") != "1.0":
        raise ValueError(f"{index_path}: unsupported schema_version")
    total = _non_negative_int(
        index.get("total_records"), source=index_path.as_posix(), field="total_records"
    )
    parts = index.get("parts")
    if not isinstance(parts, list):
        raise ValueError(f"{index_path}: parts must be an array")

    rows: list[tuple[Mapping[str, Any], str]] = []
    expected_offset = 0
    seen: set[Path] = set()
    for part_index, raw_part in enumerate(parts):
        source = f"{index_path.as_posix()}#part-{part_index}"
        part = _require_mapping(raw_part, source=source)
        declared = _non_negative_int(part.get("records"), source=source, field="records")
        if require_offsets:
            offset = _non_negative_int(part.get("offset"), source=source, field="offset")
            if offset != expected_offset:
                raise ValueError(f"{source}: offset {offset} != expected {expected_offset}")

        path = _safe_part(parts_root, part.get("file"), source=source, field="part file")
        if path in seen:
            raise ValueError(f"{source}: duplicate part file {path.name!r}")
        seen.add(path)

        part_rows = load_part(path)
        if len(part_rows) != declared:
            raise ValueError(f"{path}: declared {declared} records != {len(part_rows)} rows")
        rows.extend(part_rows)
        expected_offset += declared

    if len(rows) != total:
        raise ValueError(f"{index_path}: total_records {total} != {len(rows)} rows")
    return rows


def _json_partition_rows(
    index_path: Path,
    parts_root: Path,
    *,
    payload_key: str | None,
    require_offsets: bool,
) -> list[tuple[Mapping[str, Any], str]]:
    def load_part(path: Path) -> list[tuple[Mapping[str, Any], str]]:
        payload = _load_json(path)
        if payload_key is None:
            if not isinstance(payload, list):
                raise ValueError(f"{path}: part payload must be an array")
            raw_rows = payload
        else:
            document = _require_mapping(payload, source=path.as_posix())
            if document.get("schema_version") != "1.0":
                raise ValueError(f"{path}: unsupported schema_version")
            raw_rows = document.get(payload_key)
            if not isinstance(raw_rows, list):
                raise ValueError(f"{path}: {payload_key} must be an array")

        return [
            (
                _require_mapping(raw_row, source=f"{path.as_posix()}#row-{row_index}"),
                f"{path.as_posix()}#row-{row_index}",
            )
            for row_index, raw_row in enumerate(raw_rows)
        ]

    return _partition_rows(
        index_path,
        parts_root,
        require_offsets=require_offsets,
        load_part=load_part,
    )


def _jsonl_partition_rows(
    index_path: Path,
    parts_root: Path,
) -> list[tuple[Mapping[str, Any], str]]:
    return _partition_rows(
        index_path,
        parts_root,
        require_offsets=False,
        load_part=_load_jsonl,
    )

def _endpoint_records(root: Path, capture_sources: Mapping[str, str]) -> list[KnowledgeRecord]:
    corpus = root / "knowledge" / "http" / "endpoints"
    rows = _json_partition_rows(
        corpus / "index.json",
        corpus,
        payload_key="records",
        require_offsets=False,
    )
    records: list[KnowledgeRecord] = []
    for item, source in rows:
        path_pattern = _require_string(
            item.get("path_pattern"), source=source, field="path_pattern"
        )
        count = _non_negative_int(item.get("count"), source=source, field="count")
        methods = _require_mapping(item.get("methods"), source=source)
        method_names: list[str] = []
        method_total = 0
        for method, raw_count in methods.items():
            method_name = _require_string(method, source=source, field="method").upper()
            method_names.append(method_name)
            method_total += _non_negative_int(raw_count, source=source, field=f"methods.{method}")
        if method_total != count:
            raise ValueError(f"{source}: method counts {method_total} != count {count}")
        query_keys = _string_list(item.get("query_keys"), source=source, field="query_keys")
        examples = item.get("examples")
        if not isinstance(examples, list) or not examples:
            raise ValueError(f"{source}: examples must be a non-empty array")
        evidence = tuple(
            _observation_evidence(
                example,
                capture_sources,
                source=f"{source}.examples[{index}]",
            )
            for index, example in enumerate(examples)
        )
        methods_sorted = tuple(sorted(set(method_names)))
        ref = _versioned_ref(RefKind.ENDPOINT, {"path_pattern": path_pattern})
        records.append(
            KnowledgeRecord(
                ref=ref,
                kind=RefKind.ENDPOINT,
                title=path_pattern,
                aliases=(path_pattern, *(f"{method} {path_pattern}" for method in methods_sorted)),
                body=" ".join((path_pattern, *methods_sorted, *sorted(set(query_keys)))),
                evidence_refs=evidence,
                source_dataset="endpoints",
            )
        )
    return records


def _operation_records(root: Path, capture_sources: Mapping[str, str]) -> list[KnowledgeRecord]:
    path = root / "knowledge" / "http" / "operation-index.json"
    document = _require_mapping(_load_json(path), source=path.as_posix())
    if document.get("schema_version") != "1.0":
        raise ValueError(f"{path}: unsupported schema_version")
    post_count = _non_negative_int(
        document.get("post_count"), source=path.as_posix(), field="post_count"
    )
    operations = document.get("operations")
    if not isinstance(operations, list):
        raise ValueError(f"{path}: operations must be an array")

    records: list[KnowledgeRecord] = []
    observed_total = 0
    source_ids: set[str] = set()
    for index, raw in enumerate(operations):
        source = f"{path.as_posix()}#operation-{index}"
        item = _require_mapping(raw, source=source)
        source_id = _require_string(item.get("id"), source=source, field="id")
        if source_id in source_ids:
            raise ValueError(f"{source}: duplicate operation id {source_id!r}")
        source_ids.add(source_id)
        route = _require_string(item.get("path"), source=source, field="path")
        query_keys = tuple(sorted(set(_string_list(
            item.get("query_keys"), source=source, field="query_keys"
        ))))
        body_keys = tuple(sorted(set(_string_list(
            item.get("body_keys"), source=source, field="body_keys"
        ))))
        count = _non_negative_int(item.get("count"), source=source, field="count")
        observations = item.get("observations")
        if not isinstance(observations, list) or len(observations) != count:
            raise ValueError(f"{source}: observations must contain exactly count rows")
        evidence = tuple(
            _observation_evidence(
                observation,
                capture_sources,
                source=f"{source}.observations[{obs_index}]",
            )
            for obs_index, observation in enumerate(observations)
        )
        observed_total += count
        semantic = {
            "method": "POST",
            "path": route,
            "query_keys": list(query_keys),
            "body_keys": list(body_keys),
        }
        records.append(
            KnowledgeRecord(
                ref=_versioned_ref(RefKind.OPERATION, semantic),
                kind=RefKind.OPERATION,
                title=f"POST {route}",
                aliases=(source_id, route, f"POST {route}"),
                body=" ".join(("POST", route, *query_keys, *body_keys, source_id)),
                evidence_refs=evidence,
                source_dataset="operations",
            )
        )
    if observed_total != post_count:
        raise ValueError(f"{path}: operation counts {observed_total} != post_count {post_count}")
    return records


def _form_records(root: Path, capture_sources: Mapping[str, str]) -> list[KnowledgeRecord]:
    corpus = root / "knowledge" / "http" / "forms"
    rows = _json_partition_rows(
        corpus / "index.json",
        corpus,
        payload_key=None,
        require_offsets=True,
    )
    records: list[KnowledgeRecord] = []
    for item, source in rows:
        form_id = _require_string(item.get("form_id"), source=source, field="form_id")
        if _FORM_ID_RE.fullmatch(form_id) is None:
            raise ValueError(f"{source}: form_id must be 16 lowercase hexadecimal characters")
        method = _require_string(item.get("method"), source=source, field="method").upper()
        raw_action = _require_string(item.get("action"), source=source, field="action")
        parsed = urlsplit(raw_action)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError(f"{source}: form action must be an origin-relative URL")
        route = parsed.path
        if not route.startswith("/"):
            raise ValueError(f"{source}: form action path must start with '/'")
        query_keys = tuple(sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}))

        fields = item.get("fields")
        if not isinstance(fields, list):
            raise ValueError(f"{source}: fields must be an array")
        field_terms: set[str] = set()
        for field_index, raw_field in enumerate(fields):
            field_source = f"{source}.fields[{field_index}]"
            field = _require_mapping(raw_field, source=field_source)
            name = _require_string(field.get("name"), source=field_source, field="name")
            raw_type = field.get("type", "")
            if raw_type is None:
                field_type = ""
            elif isinstance(raw_type, str):
                field_type = raw_type.strip()
            else:
                raise ValueError(f"{field_source}: type must be a string or null")
            field_terms.add(name)
            if field_type:
                field_terms.add(f"{name}:{field_type}")

        observed_on = item.get("observed_on")
        if not isinstance(observed_on, list) or not observed_on:
            raise ValueError(f"{source}: observed_on must be a non-empty array")
        evidence = tuple(
            _observation_evidence(
                observation,
                capture_sources,
                source=f"{source}.observed_on[{obs_index}]",
            )
            for obs_index, observation in enumerate(observed_on)
        )
        records.append(
            KnowledgeRecord(
                ref=f"bm.form.v1.{form_id}",
                kind=RefKind.FORM,
                title=f"{method} {route}",
                aliases=(form_id, route, f"{method} {route}"),
                body=" ".join(
                    (method, route, *query_keys, *sorted(field_terms))
                ),
                evidence_refs=evidence,
                source_dataset="forms",
            )
        )
    return records


def _wiki_records(root: Path, capture_sources: Mapping[str, str]) -> list[KnowledgeRecord]:
    corpus = root / "knowledge" / "wiki" / "topics"
    rows = _jsonl_partition_rows(corpus / "index.json", corpus)
    records: list[KnowledgeRecord] = []
    for item, source in rows:
        topic = _require_string(item.get("topic"), source=source, field="topic")
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            raise ValueError(f"{source}: text must be a string")
        text = raw_text.strip()
        related = _string_list(item.get("related_topics"), source=source, field="related_topics")
        source_info = _require_mapping(item.get("source"), source=f"{source}.source")
        source_path = _require_string(
            source_info.get("path"), source=f"{source}.source", field="path"
        )
        raw_query = source_info.get("query", {})
        query = _require_mapping(raw_query, source=f"{source}.source.query")
        query_identity: dict[str, str] = {}
        for raw_key, raw_value in query.items():
            key = _require_string(
                raw_key, source=f"{source}.source.query", field="query key"
            )
            if not isinstance(raw_value, str):
                raise ValueError(
                    f"{source}.source.query: value for {key!r} must be a string"
                )
            query_identity[key] = raw_value
        html_sha256 = _require_string(
            source_info.get("html_sha256"), source=f"{source}.source", field="html_sha256"
        )
        if _SHA256_RE.fullmatch(html_sha256) is None:
            raise ValueError(f"{source}.source: html_sha256 must be 64 lowercase hex characters")
        evidence = (
            _observation_evidence(
                source_info,
                capture_sources,
                source=f"{source}.source",
            ),
        )
        records.append(
            KnowledgeRecord(
                ref=_versioned_ref(
                    RefKind.WIKI,
                    {
                        "path": source_path,
                        "query": dict(sorted(query_identity.items())),
                    },
                ),
                kind=RefKind.WIKI,
                title=topic,
                aliases=(topic,),
                body=" ".join((topic, *related, text)),
                evidence_refs=evidence,
                source_dataset="wiki",
            )
        )
    return records


def project_additional_curated_records(repo_root: Path) -> tuple[KnowledgeRecord, ...]:
    root = Path(repo_root).expanduser().resolve(strict=True)
    capture_sources = _capture_source_map(root)
    return tuple(
        sorted(
            (
                *_endpoint_records(root, capture_sources),
                *_operation_records(root, capture_sources),
                *_form_records(root, capture_sources),
                *_wiki_records(root, capture_sources),
            ),
            key=lambda item: item.ref,
        )
    )


__all__ = ["project_additional_curated_records"]