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
from bizman.readmodel.runtime import (
    ChangeIndexRecord,
    RuntimeProjection,
    SessionSummary,
    project_runtime_intelligence,
)
from bizman.readmodel.store import (
    INDEX_APPLICATION_ID,
    INDEX_SCHEMA_VERSION,
    KnowledgeIndex,
    rebuild_agent_index,
    rebuild_knowledge_index,
)

__all__ = [
    "EvaluationCase",
    "ChangeIndexRecord",
    "EvaluationMetrics",
    "INDEX_APPLICATION_ID",
    "INDEX_SCHEMA_VERSION",
    "KnowledgeIndex",
    "KnowledgeProjection",
    "KnowledgeRecord",
    "MatchKind",
    "RefKind",
    "SearchHit",
    "RuntimeProjection",
    "SearchQuery",
    "SessionSummary",
    "evaluate_retrieval",
    "project_curated_knowledge",
    "project_runtime_intelligence",
    "rebuild_agent_index",
    "rebuild_knowledge_index",
]
