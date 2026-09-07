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
class EndpointVariant:
    """One exactly observed structural endpoint outcome."""

    query_keys: tuple[str, ...]
    status: int


@dataclass(frozen=True, slots=True, order=True)
class EndpointMethodContract:
    """Observed variants for one HTTP method within an endpoint family."""

    method: str
    variants: tuple[EndpointVariant, ...]

    @property
    def query_key_sets(self) -> tuple[tuple[str, ...], ...]:
        return tuple(sorted({variant.query_keys for variant in self.variants}))

    @property
    def statuses(self) -> tuple[int, ...]:
        return tuple(sorted({variant.status for variant in self.variants}))


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


@dataclass(frozen=True, slots=True, order=True)
class RuleDescriptor:
    """Stable rule metadata participating in analysis-profile identity."""

    rule_id: str
    version: int
    kind: str


@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    """Replay namespace for one complete deterministic detector interpretation."""

    baseline_sha256: str
    contract_schema_version: int
    normalization_version: int
    extraction_version: int
    redaction_policy_sha256: str
    rules: tuple[RuleDescriptor, ...]
    sha256: str


__all__ = [
    "ActionRequestFamily",
    "AnalysisProfile",
    "EndpointFamily",
    "EndpointMethodContract",
    "EndpointVariant",
    "FormSignature",
    "MatchState",
    "OperationSignature",
    "PathMatch",
    "RuleDescriptor",
    "RuntimeContract",
]
