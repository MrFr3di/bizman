from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MatchState(StrEnum):
    """Result of comparing a runtime observation with curated knowledge."""

    KNOWN = "known"
    NOVEL = "novel"
    INDETERMINATE = "indeterminate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class PathMatch:
    """Deterministic result of matching one origin-relative pathname."""

    matched: bool
    path_pattern: str | None = None
    exact: bool = False


@dataclass(frozen=True, slots=True, order=True)
class EndpointMethodContract:
    """Observed method-specific behavior for one endpoint path family."""

    method: str
    query_key_sets: tuple[tuple[str, ...], ...]
    statuses: tuple[int, ...]


@dataclass(frozen=True, slots=True, order=True)
class EndpointFamily:
    path_pattern: str
    methods: tuple[EndpointMethodContract, ...]


@dataclass(frozen=True, slots=True, order=True)
class FormSignature:
    method: str
    action_path: str
    field_names: tuple[str, ...]


@dataclass(frozen=True, slots=True, order=True)
class OperationSignature:
    method: str
    path_pattern: str
    query_keys: tuple[str, ...]
    body_keys: tuple[str, ...]
    statuses: tuple[int, ...]


@dataclass(frozen=True, slots=True, order=True)
class ActionRequestFamily:
    action_id: str
    method: str
    path_pattern: str
    field_names: tuple[str, ...]
    query_key_sets: tuple[tuple[str, ...], ...]
    statuses: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    """Compiled value-free semantic baseline used by the detector."""

    contract_schema_version: int
    normalization_version: int
    endpoints: tuple[EndpointFamily, ...]
    forms: tuple[FormSignature, ...]
    operations: tuple[OperationSignature, ...]
    actions: tuple[ActionRequestFamily, ...]


__all__ = [
    "ActionRequestFamily",
    "EndpointFamily",
    "EndpointMethodContract",
    "FormSignature",
    "MatchState",
    "OperationSignature",
    "PathMatch",
    "RuntimeContract",
]
