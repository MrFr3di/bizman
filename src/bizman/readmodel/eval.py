from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bizman.readmodel.model import RefKind, SearchQuery
from bizman.readmodel.store import KnowledgeIndex


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    query: str
    expected_ref: str | None
    expected_evidence_ref: str | None
    category: str = "legacy"
    kinds: tuple[RefKind, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise TypeError("query must be a non-empty string")
        if not isinstance(self.category, str) or not self.category.strip():
            raise TypeError("category must be a non-empty string")

        if self.expected_ref is None:
            if self.expected_evidence_ref is not None:
                raise ValueError(
                    "negative evaluation cases cannot declare expected_evidence_ref"
                )
        else:
            if not isinstance(self.expected_ref, str) or not self.expected_ref:
                raise TypeError("expected_ref must be a non-empty string or None")
            if (
                not isinstance(self.expected_evidence_ref, str)
                or not self.expected_evidence_ref
            ):
                raise TypeError(
                    "positive evaluation cases require expected_evidence_ref"
                )

        normalized_kinds: list[RefKind] = []
        for kind in tuple(self.kinds):
            if isinstance(kind, RefKind):
                value = kind
            elif isinstance(kind, str):
                try:
                    value = RefKind(kind)
                except ValueError as exc:
                    raise ValueError(f"unsupported evaluation kind: {kind!r}") from exc
            else:
                raise TypeError("kinds must contain RefKind values or strings")
            if value not in normalized_kinds:
                normalized_kinds.append(value)

        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "category", self.category.strip())
        object.__setattr__(self, "kinds", tuple(normalized_kinds))


@dataclass(frozen=True, slots=True)
class EvaluationSliceMetrics:
    category: str
    cases: int
    positive_cases: int
    negative_cases: int
    recall_at_1: float | None
    recall_at_5: float | None
    mrr: float | None
    evidence_correctness: float | None
    no_match_accuracy: float | None
    overall_accuracy: float


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    cases: int
    positive_cases: int
    negative_cases: int
    recall_at_1: float | None
    recall_at_5: float | None
    mrr: float | None
    evidence_correctness: float | None
    no_match_accuracy: float | None
    overall_accuracy: float
    by_category: tuple[EvaluationSliceMetrics, ...]


@dataclass(frozen=True, slots=True)
class _CaseOutcome:
    case: EvaluationCase
    rank: int | None
    evidence_ok: bool
    negative_ok: bool


def _ratio(value: int | float, total: int) -> float | None:
    return value / total if total else None


def _metrics_for(
    outcomes: tuple[_CaseOutcome, ...],
    *,
    category: str,
) -> EvaluationSliceMetrics:
    positive = tuple(item for item in outcomes if item.case.expected_ref is not None)
    negative = tuple(item for item in outcomes if item.case.expected_ref is None)

    top1 = sum(item.rank == 1 for item in positive)
    top5 = sum(item.rank is not None and item.rank <= 5 for item in positive)
    reciprocal_rank = sum(
        1.0 / item.rank
        for item in positive
        if item.rank is not None and item.rank <= 5
    )
    evidence_ok = sum(item.evidence_ok for item in positive)
    negative_ok = sum(item.negative_ok for item in negative)
    correct = top1 + negative_ok

    return EvaluationSliceMetrics(
        category=category,
        cases=len(outcomes),
        positive_cases=len(positive),
        negative_cases=len(negative),
        recall_at_1=_ratio(top1, len(positive)),
        recall_at_5=_ratio(top5, len(positive)),
        mrr=_ratio(reciprocal_rank, len(positive)),
        evidence_correctness=_ratio(evidence_ok, len(positive)),
        no_match_accuracy=_ratio(negative_ok, len(negative)),
        overall_accuracy=correct / len(outcomes),
    )


def evaluation_cases_from_document(document: object) -> tuple[EvaluationCase, ...]:
    if not isinstance(document, dict):
        raise TypeError("evaluation fixture must be an object")
    schema_version = document.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise TypeError("evaluation schema_version must be an integer")
    if schema_version not in {1, 2, 3}:
        raise ValueError(f"unsupported evaluation schema_version: {schema_version}")

    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("evaluation fixture cases must be a non-empty array")

    cases: list[EvaluationCase] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise TypeError(f"evaluation case {index} must be an object")
        allowed = {
            "query",
            "expected_ref",
            "expected_evidence_ref",
            "category",
            "kinds",
        }
        unknown = set(raw_case) - allowed
        if unknown:
            raise ValueError(
                f"evaluation case {index} contains unsupported fields: "
                f"{sorted(unknown)}"
            )
        values: dict[str, Any] = dict(raw_case)
        if schema_version < 3:
            values.setdefault("category", "legacy")
            values.setdefault("kinds", ())
        else:
            values.setdefault("expected_ref", None)
            values.setdefault("expected_evidence_ref", None)
            values.setdefault("category", "uncategorized")
            values.setdefault("kinds", ())
        cases.append(EvaluationCase(**values))
    return tuple(cases)


def evaluate_retrieval(
    index: KnowledgeIndex,
    cases: tuple[EvaluationCase, ...],
) -> EvaluationMetrics:
    if not isinstance(index, KnowledgeIndex):
        raise TypeError("index must be KnowledgeIndex")
    cases = tuple(cases)
    if not cases:
        raise ValueError("evaluation cases must not be empty")
    if not all(isinstance(case, EvaluationCase) for case in cases):
        raise TypeError("cases must contain EvaluationCase values")

    outcomes: list[_CaseOutcome] = []
    for case in cases:
        hits = index.search(
            SearchQuery(
                case.query,
                limit=5,
                kinds=case.kinds,
            )
        )
        if case.expected_ref is None:
            outcomes.append(
                _CaseOutcome(
                    case=case,
                    rank=None,
                    evidence_ok=False,
                    negative_ok=not hits,
                )
            )
            continue

        rank: int | None = None
        expected_hit = None
        for index_position, hit in enumerate(hits, 1):
            if hit.ref == case.expected_ref:
                rank = index_position
                expected_hit = hit
                break
        outcomes.append(
            _CaseOutcome(
                case=case,
                rank=rank,
                evidence_ok=(
                    expected_hit is not None
                    and case.expected_evidence_ref in expected_hit.evidence_refs
                ),
                negative_ok=False,
            )
        )

    frozen_outcomes = tuple(outcomes)
    overall = _metrics_for(frozen_outcomes, category="all")
    categories = tuple(sorted({case.category for case in cases}))
    by_category = tuple(
        _metrics_for(
            tuple(
                outcome
                for outcome in frozen_outcomes
                if outcome.case.category == category
            ),
            category=category,
        )
        for category in categories
    )
    return EvaluationMetrics(
        cases=overall.cases,
        positive_cases=overall.positive_cases,
        negative_cases=overall.negative_cases,
        recall_at_1=overall.recall_at_1,
        recall_at_5=overall.recall_at_5,
        mrr=overall.mrr,
        evidence_correctness=overall.evidence_correctness,
        no_match_accuracy=overall.no_match_accuracy,
        overall_accuracy=overall.overall_accuracy,
        by_category=by_category,
    )


__all__ = [
    "EvaluationCase",
    "EvaluationMetrics",
    "EvaluationSliceMetrics",
    "evaluate_retrieval",
    "evaluation_cases_from_document",
]
