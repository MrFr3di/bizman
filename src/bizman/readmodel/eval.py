from __future__ import annotations

from dataclasses import dataclass

from bizman.readmodel.model import SearchQuery
from bizman.readmodel.store import KnowledgeIndex


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    query: str
    expected_ref: str
    expected_evidence_ref: str

    def __post_init__(self) -> None:
        for name, value in (
            ("query", self.query),
            ("expected_ref", self.expected_ref),
            ("expected_evidence_ref", self.expected_evidence_ref),
        ):
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    cases: int
    recall_at_1: float
    recall_at_5: float
    mrr: float
    evidence_correctness: float


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

    top1 = 0
    top5 = 0
    reciprocal_rank = 0.0
    evidence_ok = 0

    for case in cases:
        hits = index.search(SearchQuery(case.query, limit=5))
        rank: int | None = None
        expected_hit = None
        for index_position, hit in enumerate(hits, 1):
            if hit.ref == case.expected_ref:
                rank = index_position
                expected_hit = hit
                break
        if rank == 1:
            top1 += 1
        if rank is not None and rank <= 5:
            top5 += 1
            reciprocal_rank += 1.0 / rank
        if (
            expected_hit is not None
            and case.expected_evidence_ref in expected_hit.evidence_refs
        ):
            evidence_ok += 1

    count = len(cases)
    return EvaluationMetrics(
        cases=count,
        recall_at_1=top1 / count,
        recall_at_5=top5 / count,
        mrr=reciprocal_rank / count,
        evidence_correctness=evidence_ok / count,
    )


__all__ = ["EvaluationCase", "EvaluationMetrics", "evaluate_retrieval"]
