from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from bizman.foundation.fingerprint import canonical_sha256
from bizman.readmodel.corpora import project_additional_curated_records
from bizman.readmodel.model import KnowledgeRecord, RefKind


def _record_semantics(record: KnowledgeRecord) -> dict[str, object]:
    return {
        "ref": record.ref,
        "kind": record.kind.value,
        "title": record.title,
        "aliases": list(record.aliases),
        "body": record.body,
        "evidence_refs": list(record.evidence_refs),
        "source_dataset": record.source_dataset,
    }


def _projection_fingerprint(records: tuple[KnowledgeRecord, ...]) -> str:
    ordered = tuple(sorted(records, key=lambda item: item.ref))
    return canonical_sha256([_record_semantics(record) for record in ordered])


@dataclass(frozen=True, slots=True)
class KnowledgeProjection:
    records: tuple[KnowledgeRecord, ...]
    source_fingerprint: str

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if not all(isinstance(record, KnowledgeRecord) for record in records):
            raise TypeError("records must contain only KnowledgeRecord values")
        if len({record.ref for record in records}) != len(records):
            raise ValueError("knowledge projection contains duplicate refs")
        ordered = tuple(sorted(records, key=lambda item: item.ref))
        expected_fingerprint = _projection_fingerprint(ordered)
        if self.source_fingerprint != expected_fingerprint:
            raise ValueError("source_fingerprint does not match projected record semantics")
        object.__setattr__(self, "records", ordered)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load curated knowledge file {path}: {exc}") from exc


def _require_mapping(value: object, *, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{source}: expected JSON object")
    return value


def _require_string(value: object, *, source: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {field} must be a non-empty string")
    return value.strip()


def _non_negative_int(value: object, *, source: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{source}: {field} must be a non-negative integer")
    return value


def _string_list(value: object, *, source: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{source}: {field} must be an array of non-empty strings")
    return tuple(value)


def _safe_child(root: Path, relative: object, *, source: str) -> Path:
    text = _require_string(relative, source=source, field="part path")
    candidate = (root / text).resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise ValueError(f"{source}: part path escapes products directory")
    return candidate


def _action_records(root: Path) -> list[KnowledgeRecord]:
    path = root / "knowledge" / "actions" / "catalog.json"
    document = _require_mapping(_load_json(path), source=path.as_posix())
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{path}: items must be an array")
    declared_count = _non_negative_int(
        document.get("count"), source=path.as_posix(), field="count"
    )
    if declared_count != len(items):
        raise ValueError(f"{path}: declared count {declared_count} != {len(items)} items")
    records: list[KnowledgeRecord] = []
    for index, raw in enumerate(items):
        source = f"{path.as_posix()}#item-{index}"
        item = _require_mapping(raw, source=source)
        ref = _require_string(item.get("id"), source=source, field="id")
        method = _require_string(item.get("method"), source=source, field="method").upper()
        route = _require_string(item.get("path"), source=source, field="path")
        evidence = _string_list(item.get("evidence"), source=source, field="evidence")
        if item.get("confidence") != "observed":
            raise ValueError(f"{source}: action confidence must be observed")

        form_fields = item.get("form_fields")
        field_names: list[str] = []
        if not isinstance(form_fields, list):
            raise ValueError(f"{source}: form_fields must be an array")
        for field_index, raw_field in enumerate(form_fields):
            field = _require_mapping(raw_field, source=f"{source}.form_fields[{field_index}]")
            field_names.append(
                _require_string(
                    field.get("name"),
                    source=f"{source}.form_fields[{field_index}]",
                    field="name",
                )
            )

        query_sets = item.get("query_key_sets")
        query_keys: set[str] = set()
        if not isinstance(query_sets, list):
            raise ValueError(f"{source}: query_key_sets must be an array")
        for query_index, raw_keys in enumerate(query_sets):
            if not isinstance(raw_keys, list) or not all(
                isinstance(key, str) and key for key in raw_keys
            ):
                raise ValueError(f"{source}.query_key_sets[{query_index}]: invalid keys")
            query_keys.update(raw_keys)

        path_segments = tuple(part for part in route.strip("/").split("/") if part)
        path_words = " ".join(path_segments)
        tail_words = " ".join(path_segments[-2:])
        aliases = (route, f"{method} {route}", path_words, tail_words)
        body = " ".join(
            value
            for value in (
                method,
                route,
                path_words,
                " ".join(sorted(query_keys)),
                " ".join(sorted(field_names)),
            )
            if value
        )
        records.append(
            KnowledgeRecord(
                ref=ref,
                kind=RefKind.ACTION,
                title=f"{method} {route}",
                aliases=aliases,
                body=body,
                evidence_refs=evidence,
                source_dataset="actions",
            )
        )
    return records


def _product_records(root: Path) -> list[KnowledgeRecord]:
    products_root = root / "knowledge" / "domain" / "products"
    index_path = products_root / "index.json"
    index = _require_mapping(_load_json(index_path), source=index_path.as_posix())
    parts = index.get("parts")
    if not isinstance(parts, list):
        raise ValueError(f"{index_path}: parts must be an array")

    records: list[KnowledgeRecord] = []
    declared_total = _non_negative_int(
        index.get("total_count"), source=index_path.as_posix(), field="total_count"
    )
    declared_part_count = _non_negative_int(
        index.get("part_count"), source=index_path.as_posix(), field="part_count"
    )
    if declared_part_count != len(parts):
        raise ValueError(
            f"{index_path}: declared part_count {declared_part_count} != {len(parts)} parts"
        )

    expected_offset = 0
    seen_part_paths: set[Path] = set()
    for part_index, raw_part in enumerate(parts):
        source = f"{index_path.as_posix()}#part-{part_index}"
        part = _require_mapping(raw_part, source=source)
        declared_offset = _non_negative_int(part.get("offset"), source=source, field="offset")
        declared_count = _non_negative_int(part.get("count"), source=source, field="count")
        if declared_offset != expected_offset:
            raise ValueError(
                f"{source}: offset {declared_offset} != expected {expected_offset}"
            )
        part_path = _safe_child(products_root, part.get("path"), source=source)
        if part_path in seen_part_paths:
            raise ValueError(f"{source}: duplicate product part path {part_path.name!r}")
        seen_part_paths.add(part_path)

        document = _require_mapping(_load_json(part_path), source=part_path.as_posix())
        items = document.get("items")
        if not isinstance(items, list):
            raise ValueError(f"{part_path}: items must be an array")
        if _non_negative_int(
            document.get("part"), source=part_path.as_posix(), field="part"
        ) != part_index:
            raise ValueError(f"{part_path}: part number does not match manifest order")
        if _non_negative_int(
            document.get("offset"), source=part_path.as_posix(), field="offset"
        ) != declared_offset:
            raise ValueError(f"{part_path}: offset disagrees with product manifest")
        if _non_negative_int(
            document.get("count"), source=part_path.as_posix(), field="count"
        ) != declared_count:
            raise ValueError(f"{part_path}: count disagrees with product manifest")
        if declared_count != len(items):
            raise ValueError(
                f"{part_path}: declared count {declared_count} != {len(items)} items"
            )
        for item_index, raw in enumerate(items):
            item_source = f"{part_path.as_posix()}#item-{item_index}"
            item = _require_mapping(raw, source=item_source)
            ref = _require_string(item.get("id"), source=item_source, field="id")
            slug = _require_string(item.get("slug"), source=item_source, field="slug")
            name = _require_string(item.get("name"), source=item_source, field="name")
            evidence = _string_list(item.get("evidence"), source=item_source, field="evidence")
            numeric_ids = item.get("numeric_ids", [])
            if not isinstance(numeric_ids, list) or not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in numeric_ids
            ):
                raise ValueError(f"{item_source}: numeric_ids must be non-negative integers")
            categories = _string_list(
                item.get("categories", []), source=item_source, field="categories"
            )
            aliases = (slug,) + tuple(f"product:{value}" for value in numeric_ids)
            records.append(
                KnowledgeRecord(
                    ref=ref,
                    kind=RefKind.PRODUCT,
                    title=name,
                    aliases=aliases,
                    body=" ".join((name, slug, *categories)),
                    evidence_refs=evidence,
                    source_dataset="products",
                )
            )
        expected_offset += declared_count

    if declared_total != len(records):
        raise ValueError(
            f"{index_path}: declared total_count {declared_total} != {len(records)} records"
        )
    return records


def _entity_kind(value: object, *, source: str) -> RefKind:
    text = _require_string(value, source=source, field="kind")
    try:
        return RefKind(text)
    except ValueError as exc:
        raise ValueError(f"{source}: unsupported entity kind {text!r}") from exc


def _entity_records(root: Path) -> list[KnowledgeRecord]:
    path = root / "knowledge" / "domain" / "entities.json"
    document = _require_mapping(_load_json(path), source=path.as_posix())
    items = document.get("entities")
    if not isinstance(items, list):
        raise ValueError(f"{path}: entities must be an array")
    records: list[KnowledgeRecord] = []
    for index, raw in enumerate(items):
        source = f"{path.as_posix()}#entity-{index}"
        item = _require_mapping(raw, source=source)
        ref = _require_string(item.get("id"), source=source, field="id")
        kind = _entity_kind(item.get("kind"), source=source)
        name = _require_string(item.get("name"), source=source, field="name")
        numeric_id = item.get("numeric_id")
        if isinstance(numeric_id, bool) or not isinstance(numeric_id, int) or numeric_id < 0:
            raise ValueError(f"{source}: numeric_id must be a non-negative integer")
        subtype = item.get("subtype")
        if subtype is not None and not isinstance(subtype, str):
            raise ValueError(f"{source}: subtype must be a string or null")
        evidence = _string_list(item.get("evidence"), source=source, field="evidence")
        aliases = (f"{kind.value}:{numeric_id}",)
        body = " ".join(value for value in (name, kind.value, subtype or "") if value)
        records.append(
            KnowledgeRecord(
                ref=ref,
                kind=kind,
                title=name,
                aliases=aliases,
                body=body,
                evidence_refs=evidence,
                source_dataset="entities",
            )
        )
    declared = document.get("count")
    if declared != len(records):
        raise ValueError(f"{path}: declared count {declared!r} != {len(records)} entities")
    return records


def project_curated_knowledge(repo_root: Path) -> KnowledgeProjection:
    root = Path(repo_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    records = tuple(
        sorted(
            (
                *_action_records(root),
                *_product_records(root),
                *_entity_records(root),
                *project_additional_curated_records(root),
            ),
            key=lambda item: item.ref,
        )
    )
    return KnowledgeProjection(
        records=records,
        source_fingerprint=_projection_fingerprint(records),
    )


__all__ = ["KnowledgeProjection", "project_curated_knowledge"]