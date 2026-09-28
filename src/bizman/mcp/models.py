from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from bizman.core import (
    KnowledgeGetResult,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeResolveResult,
    KnowledgeSearchResult,
)


KnowledgeKind = Literal[
    "action",
    "product",
    "city",
    "company",
    "unit",
    "endpoint",
    "operation",
    "form",
    "wiki_topic",
]


class EvidenceHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    kind: KnowledgeKind
    title: str
    match_kind: Literal["exact_ref", "exact_alias", "exact_title", "full_text"]
    evidence_refs: tuple[str, ...]


class EvidenceResolveResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hit: EvidenceHit | None


class EvidenceSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[EvidenceHit, ...]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    kind: KnowledgeKind
    title: str
    aliases: tuple[str, ...]
    body: str
    evidence_refs: tuple[str, ...]
    source_dataset: str


class EvidenceGetResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item: EvidenceItem | None


def _hit(value: KnowledgeHit) -> EvidenceHit:
    return EvidenceHit(
        ref=value.ref,
        kind=value.kind,
        title=value.title,
        match_kind=value.match_kind,
        evidence_refs=value.evidence_refs,
    )


def _item(value: KnowledgeItem) -> EvidenceItem:
    return EvidenceItem(
        ref=value.ref,
        kind=value.kind,
        title=value.title,
        aliases=value.aliases,
        body=value.body,
        evidence_refs=value.evidence_refs,
        source_dataset=value.source_dataset,
    )


def resolve_result(value: KnowledgeResolveResult) -> EvidenceResolveResult:
    return EvidenceResolveResult(
        hit=_hit(value.hit) if value.hit is not None else None
    )


def search_result(value: KnowledgeSearchResult) -> EvidenceSearchResult:
    return EvidenceSearchResult(items=tuple(_hit(item) for item in value.items))


def get_result(value: KnowledgeGetResult) -> EvidenceGetResult:
    return EvidenceGetResult(
        item=_item(value.item) if value.item is not None else None
    )


__all__ = [
    "EvidenceGetResult",
    "EvidenceHit",
    "EvidenceItem",
    "EvidenceResolveResult",
    "EvidenceSearchResult",
    "KnowledgeKind",
]
