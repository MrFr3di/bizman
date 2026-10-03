"""Verified goods-surface evidence and catalog resolution for Current State.

This module turns immutable unit-economics artifacts into projection inputs. It
never parses game HTML, never selects a parser from URL text alone and never
interprets omission as deletion.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping

from bizman.foundation.fingerprint import canonical_sha256
from bizman.foundation.unit_economics import (
    CONTRACT_VERSION,
    RESPONSE_BODY_KIND,
    SCHEMA,
    UnitEconomicsContractError,
    UnitEconomicsPage,
    parse_unit_economics_payload,
)
from bizman.sessions.evidence import EvidenceError, EvidenceReader


_GOODS_PATH = "/units/shop/"
SURFACE = "shop.goods"
PARSER_STALE_REASON = "unit_economics_parser_v1_incompatible"
CATALOG_RESOLVER_VERSION = 1
_UNIT_ECONOMICS_SCHEMA_PREFIX = "bizman.unit-economics."
_CATALOG_DIRECTORY = Path("knowledge") / "domain" / "products"
_CATALOG_INDEX_NAME = "index.json"
_CATALOG_KEY_RE = re.compile(r"^bm\.product\.[a-z0-9][a-z0-9._-]*$", re.ASCII)
_POSITIVE_DECIMAL_RE = re.compile(r"^[1-9][0-9]*$", re.ASCII)
_MAX_SIGNED_INT64 = (1 << 63) - 1


class UnitEconomicsArtifactError(ValueError):
    """Unit-economics evidence is malformed or violates its artifact contract."""


class UnitEconomicsParserIncompatible(ValueError):
    """Recognized unit-economics evidence no longer matches parser v1 semantics.

    The exception carries the surface context when raised by the projection so
    replay can record a deterministic stale surface without guessing.
    """

    def __init__(
        self,
        message: str,
        *,
        unit_id: str | None = None,
        session_id: str | None = None,
        sequence: int | None = None,
        observed_at: str | None = None,
        reason: str = PARSER_STALE_REASON,
    ) -> None:
        super().__init__(message)
        self.unit_id = unit_id
        self.session_id = session_id
        self.sequence = sequence
        self.observed_at = observed_at
        self.reason = reason


class CatalogResolverError(ValueError):
    """The curated product catalog cannot define a deterministic resolution."""


@dataclass(frozen=True, slots=True)
class UnitEconomicsProjection:
    page: UnitEconomicsPage
    unit_id: str
    session_id: str
    sequence: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class CatalogResolver:
    """Deterministic numeric-id to ``bm.product.*`` catalog resolution."""

    resolved: Mapping[int, str]
    ambiguous_ids: frozenset[int]
    resolver_version: int = CATALOG_RESOLVER_VERSION

    @classmethod
    def load(cls, repo_root: Path) -> "CatalogResolver":
        directory = Path(repo_root).expanduser() / _CATALOG_DIRECTORY
        index = _read_catalog_json(directory / _CATALOG_INDEX_NAME)
        parts = index.get("parts")
        if not isinstance(parts, list):
            raise CatalogResolverError("catalog index parts must be an array")
        part_names: list[str] = []
        for descriptor in parts:
            if not isinstance(descriptor, dict):
                raise CatalogResolverError("catalog part descriptor must be an object")
            name = descriptor.get("path")
            if not isinstance(name, str) or not name:
                raise CatalogResolverError("catalog part path must be a string")
            pure = PurePosixPath(name)
            if (
                pure.is_absolute()
                or len(pure.parts) != 1
                or pure.suffix != ".json"
                or pure.name != name
            ):
                raise CatalogResolverError(
                    "catalog part path must be a plain JSON file name"
                )
            part_names.append(name)

        resolved: dict[int, str] = {}
        ambiguous: set[int] = set()
        for part_name in sorted(set(part_names)):
            part = _read_catalog_json(directory / part_name)
            items = part.get("items")
            if not isinstance(items, list):
                raise CatalogResolverError(
                    "catalog part items must be an array"
                )
            for item in items:
                if not isinstance(item, dict):
                    raise CatalogResolverError(
                        "catalog item must be an object"
                    )
                catalog_key = item.get("id")
                if (
                    not isinstance(catalog_key, str)
                    or _CATALOG_KEY_RE.fullmatch(catalog_key) is None
                ):
                    raise CatalogResolverError(
                        "catalog item id must be a bm.product.* key"
                    )
                numeric_ids = item.get("numeric_ids")
                if not isinstance(numeric_ids, list):
                    raise CatalogResolverError(
                        "catalog item numeric_ids must be an array"
                    )
                for numeric_id in numeric_ids:
                    if (
                        isinstance(numeric_id, bool)
                        or not isinstance(numeric_id, int)
                        or numeric_id <= 0
                        or numeric_id > _MAX_SIGNED_INT64
                    ):
                        raise CatalogResolverError(
                            "catalog numeric id must be a positive 64-bit integer"
                        )
                    if numeric_id in ambiguous:
                        continue
                    existing = resolved.get(numeric_id)
                    if existing is None:
                        resolved[numeric_id] = catalog_key
                    elif existing != catalog_key:
                        # Multiple catalog candidates are explicitly unresolved.
                        ambiguous.add(numeric_id)
                        del resolved[numeric_id]

        return cls(
            resolved=MappingProxyType(dict(resolved)),
            ambiguous_ids=frozenset(ambiguous),
        )

    def resolve(self, product_numeric_id: int) -> tuple[str | None, str]:
        if (
            isinstance(product_numeric_id, bool)
            or not isinstance(product_numeric_id, int)
        ):
            raise TypeError("product_numeric_id must be an integer")
        if product_numeric_id in self.ambiguous_ids:
            return None, "unresolved"
        catalog_key = self.resolved.get(product_numeric_id)
        if catalog_key is None:
            return None, "unresolved"
        return catalog_key, "resolved"

    def semantic_fingerprint(self) -> str:
        return canonical_sha256(
            {
                "resolver": "bizman.current.products.catalog",
                "resolver_version": self.resolver_version,
                "mapping": {
                    str(numeric_id): catalog_key
                    for numeric_id, catalog_key in sorted(self.resolved.items())
                },
                "ambiguous_ids": sorted(
                    str(numeric_id) for numeric_id in self.ambiguous_ids
                ),
            }
        )


def _read_catalog_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogResolverError(
            "curated product catalog is unavailable"
        ) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogResolverError(
            "curated product catalog is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise CatalogResolverError(
            "curated product catalog root must be an object"
        )
    return value


def catalog_resolver_sha256(repo_root: Path) -> str:
    """Hash only the normalization-relevant catalog mapping semantics."""

    return CatalogResolver.load(repo_root).semantic_fingerprint()


def _query_values(event: Mapping[str, object], key: str) -> list[str] | None:
    query = event.get("query")
    if not isinstance(query, Mapping):
        return None
    value = query.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    return value


def _goods_unit_id(event: Mapping[str, object]) -> str | None:
    if event.get("event_type") != "http.response_body":
        return None
    if event.get("source") != "cdp.network":
        return None
    if event.get("method") != "GET" or event.get("url_path") != _GOODS_PATH:
        return None
    if event.get("status_code") != 200:
        return None
    query = event.get("query")
    if not isinstance(query, Mapping) or set(query) != {"id", "tab"}:
        return None
    unit_ids = _query_values(event, "id")
    tabs = _query_values(event, "tab")
    if (
        unit_ids is None
        or tabs is None
        or len(unit_ids) != 1
        or len(tabs) != 1
        or tabs[0] != "goods"
    ):
        return None
    unit_id = unit_ids[0]
    if (
        _POSITIVE_DECIMAL_RE.fullmatch(unit_id) is None
        or int(unit_id) > _MAX_SIGNED_INT64
    ):
        return None
    return unit_id


def is_unit_goods_event(event: Mapping[str, object]) -> bool:
    """Recognize an exact goods-surface event without inferring from URL text.

    The event must carry the unit-economics artifact kind marker; the artifact
    schema check happens when the referenced payload is loaded.
    """

    if _goods_unit_id(event) is None:
        return False
    return event.get("response_body_kind") == RESPONSE_BODY_KIND


def _event_provenance(
    event: Mapping[str, object],
) -> tuple[str, int, str]:
    session_id = event.get("session_id")
    sequence = event.get("sequence")
    observed_at = event.get("observed_at")
    if (
        not isinstance(session_id, str)
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(observed_at, str)
    ):
        raise UnitEconomicsArtifactError(
            "validated unit-economics event lacks canonical provenance fields"
        )
    return session_id, sequence, observed_at


def _load_artifact(reader: EvidenceReader, ref: object) -> bytes:
    if not isinstance(ref, str):
        raise UnitEconomicsArtifactError(
            "unit economics response_body_ref must be a verified CAS reference"
        )
    try:
        return reader.read_verified_artifact(ref)
    except EvidenceError as exc:
        raise UnitEconomicsArtifactError(
            "unit economics artifact reference is not verifiable"
        ) from exc


def _is_recognized_incompatible_contract(raw: bytes) -> bool:
    """Return True only for a recognized unit-economics artifact of another contract."""

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    schema = value.get("schema")
    if not isinstance(schema, str) or not schema.startswith(
        _UNIT_ECONOMICS_SCHEMA_PREFIX
    ):
        return False
    if schema == SCHEMA and value.get("contract_version") == CONTRACT_VERSION:
        return False
    return True


def project_unit_economics_event(
    reader: EvidenceReader,
    event: Mapping[str, object],
) -> UnitEconomicsProjection | None:
    """Load and validate one goods event, or return None when unrecognized."""

    unit_id = _goods_unit_id(event)
    if unit_id is None or not is_unit_goods_event(event):
        return None
    session_id, sequence, observed_at = _event_provenance(event)
    raw = _load_artifact(reader, event.get("response_body_ref"))
    try:
        page = parse_unit_economics_payload(raw)
    except UnitEconomicsContractError as exc:
        if _is_recognized_incompatible_contract(raw):
            raise UnitEconomicsParserIncompatible(
                "unit economics artifact contract is incompatible",
                unit_id=unit_id,
                session_id=session_id,
                sequence=sequence,
                observed_at=observed_at,
            ) from exc
        raise UnitEconomicsArtifactError(
            "unit economics artifact violates the typed contract"
        ) from exc
    if page.unit_id != unit_id:
        raise UnitEconomicsArtifactError(
            "unit economics artifact unit id disagrees with its event"
        )
    return UnitEconomicsProjection(
        page=page,
        unit_id=unit_id,
        session_id=session_id,
        sequence=sequence,
        observed_at=observed_at,
    )


__all__ = [
    "CATALOG_RESOLVER_VERSION",
    "PARSER_STALE_REASON",
    "SURFACE",
    "CatalogResolver",
    "CatalogResolverError",
    "UnitEconomicsArtifactError",
    "UnitEconomicsParserIncompatible",
    "UnitEconomicsProjection",
    "catalog_resolver_sha256",
    "is_unit_goods_event",
    "project_unit_economics_event",
]
