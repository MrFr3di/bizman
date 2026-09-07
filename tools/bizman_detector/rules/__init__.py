from __future__ import annotations

from dataclasses import replace

from tools.bizman_detector import EXTRACTION_VERSION, NORMALIZATION_VERSION
from tools.bizman_detector.model import DiffFact, Finding, MatchState, RuleDescriptor
from tools.bizman_detector.rules.forms import FORM_RULES
from tools.bizman_detector.rules.http import HTTP_RULES
from tools.bizman_detector.rules.operations import OPERATION_RULES
from tools.bizman_detector.rules.relations import RELATION_RULES
from tools.bizman_foundation.fingerprint import canonical_sha256


RULE_DESCRIPTORS: tuple[RuleDescriptor, ...] = tuple(
    sorted(
        (*HTTP_RULES, *FORM_RULES, *OPERATION_RULES, *RELATION_RULES),
        key=lambda descriptor: descriptor.rule_id,
    )
)


class RuleConfigurationError(ValueError):
    """Rule catalog or semantic version configuration is invalid."""


def _positive_version(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuleConfigurationError(f"{name} must be a positive integer")
    return value


def _validated_descriptors(
    descriptors: tuple[RuleDescriptor, ...],
) -> tuple[RuleDescriptor, ...]:
    if not isinstance(descriptors, tuple):
        descriptors = tuple(descriptors)

    by_id: dict[str, RuleDescriptor] = {}
    by_kind: dict[str, RuleDescriptor] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, RuleDescriptor):
            raise RuleConfigurationError("rule catalog entries must be RuleDescriptor values")
        if not descriptor.rule_id:
            raise RuleConfigurationError("rule_id must be non-empty")
        if not descriptor.kind:
            raise RuleConfigurationError(f"rule {descriptor.rule_id!r} has an empty kind")
        _positive_version(descriptor.version, name=f"rule {descriptor.rule_id!r} version")
        if descriptor.rule_id in by_id:
            raise RuleConfigurationError(f"duplicate rule_id {descriptor.rule_id!r}")
        if descriptor.kind in by_kind:
            raise RuleConfigurationError(f"duplicate rule kind {descriptor.kind!r}")
        by_id[descriptor.rule_id] = descriptor
        by_kind[descriptor.kind] = descriptor
    return tuple(sorted(by_id.values(), key=lambda descriptor: descriptor.rule_id))


def _evidence_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise RuleConfigurationError("evidence event IDs must be non-empty strings")
        result.add(value)
    return tuple(sorted(result))


class RuleEngine:
    """Pure deterministic mapping from semantic DiffFacts to stable Findings."""

    def __init__(
        self,
        descriptors: tuple[RuleDescriptor, ...] = RULE_DESCRIPTORS,
        *,
        normalization_version: int = NORMALIZATION_VERSION,
        extraction_version: int = EXTRACTION_VERSION,
    ) -> None:
        self.descriptors = _validated_descriptors(tuple(descriptors))
        self.normalization_version = _positive_version(
            normalization_version, name="normalization_version"
        )
        self.extraction_version = _positive_version(
            extraction_version, name="extraction_version"
        )
        self._by_kind = {descriptor.kind: descriptor for descriptor in self.descriptors}

    def _finding(self, fact: DiffFact, descriptor: RuleDescriptor) -> Finding:
        novelty_class = fact.state.value
        identity = {
            "normalization_version": self.normalization_version,
            "extraction_version": self.extraction_version,
            "rule_id": descriptor.rule_id,
            "rule_version": descriptor.version,
            "kind": descriptor.kind,
            "novelty_class": novelty_class,
            "subject": fact.subject,
            "delta": fact.delta,
        }
        return Finding(
            change_id=f"chg.{canonical_sha256(identity)}",
            rule_id=descriptor.rule_id,
            rule_version=descriptor.version,
            kind=descriptor.kind,
            novelty_class=novelty_class,
            subject=fact.subject,
            delta=fact.delta,
            evidence_event_ids=_evidence_ids(fact.evidence_event_ids),
        )

    def apply(self, facts: tuple[DiffFact, ...]) -> tuple[Finding, ...]:
        merged: dict[str, Finding] = {}
        for fact in facts:
            if not isinstance(fact, DiffFact):
                raise TypeError("facts must contain DiffFact values")
            if fact.state not in {MatchState.NOVEL, MatchState.CONFLICT}:
                continue
            descriptor = self._by_kind.get(fact.kind)
            if descriptor is None:
                continue

            finding = self._finding(fact, descriptor)
            existing = merged.get(finding.change_id)
            if existing is None:
                merged[finding.change_id] = finding
                continue

            evidence = tuple(
                sorted(set(existing.evidence_event_ids).union(finding.evidence_event_ids))
            )
            if evidence != existing.evidence_event_ids:
                merged[finding.change_id] = replace(existing, evidence_event_ids=evidence)

        return tuple(merged[key] for key in sorted(merged))


__all__ = [
    "RULE_DESCRIPTORS",
    "RuleConfigurationError",
    "RuleEngine",
]
