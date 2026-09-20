"""Deterministic derived read model for BizMan knowledge and session intelligence."""

from bizman.readmodel.eval import EvaluationCase, EvaluationMetrics, evaluate_retrieval
from bizman.readmodel.knowledge import KnowledgeProjection, project_curated_knowledge
from bizman.readmodel.model import (
    KnowledgeRecord,
    MatchKind,
    RefKind,
    SearchHit,
    SearchQuery,
)
from bizman.readmodel.store import (
    INDEX_APPLICATION_ID,
    INDEX_SCHEMA_VERSION,
    KnowledgeIndex,
    rebuild_knowledge_index,
)

__all__ = [
    "EvaluationCase",
    "EvaluationMetrics",
    "INDEX_APPLICATION_ID",
    "INDEX_SCHEMA_VERSION",
    "KnowledgeIndex",
    "KnowledgeProjection",
    "KnowledgeRecord",
    "MatchKind",
    "RefKind",
    "SearchHit",
    "SearchQuery",
    "evaluate_retrieval",
    "project_curated_knowledge",
    "rebuild_knowledge_index",
]
