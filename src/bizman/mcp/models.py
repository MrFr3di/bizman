from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from bizman.core import (
    ChangeGetResult as CoreChangeGetResult,
    ChangePage as CoreChangePage,
    ChangeRecord as CoreChangeRecord,
    EvidenceTraceResult as CoreEvidenceTraceResult,
    KnowledgeGetResult,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeResolveResult,
    KnowledgeSearchResult,
    SessionGetResult as CoreSessionGetResult,
    SessionPage as CoreSessionPage,
    SessionRecord as CoreSessionRecord,
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


class EvidenceTraceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_ref: str
    source_id: str
    source_kind: Literal["har_capture", "promoted_session"]
    locator_kind: Literal["entry", "sequence"]
    ordinal: int
    source_record_count: int
    raw_source_committed: bool
    source_sha256: str | None
    runtime_session_id: str | None
    observed_from: str
    observed_to: str | None
    privacy: str
    provenance_policy: str


class EvidenceTraceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace: EvidenceTraceItem | None


class SessionSummaryItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    manifest_sha256: str
    evidence_sha256: str
    started_at: str
    ended_at: str
    status: str
    event_count: int
    action_count: int
    http_request_count: int
    http_response_count: int
    correlation_strong_count: int
    correlation_probable_count: int
    correlation_temporal_count: int
    correlation_exact_count: int
    uncorrelated_action_count: int
    warning_count: int
    anomaly_count: int


class SessionListResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[SessionSummaryItem, ...]
    next_cursor: str | None = None


class SessionSummaryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: SessionSummaryItem | None


class ChangeItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_profile_sha256: str
    change_id: str
    rule_id: str
    rule_version: int
    kind: str
    novelty_class: str
    first_session_id: str
    first_seen_at: str
    last_session_id: str
    last_seen_at: str
    occurrence_count: int


class ChangeListResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ChangeItem, ...]
    next_cursor: str | None = None


class ChangeGetResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    change: ChangeItem | None


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


def _session(value: CoreSessionRecord) -> SessionSummaryItem:
    return SessionSummaryItem(
        **{
            field: getattr(value, field)
            for field in SessionSummaryItem.model_fields
        }
    )


def _change(value: CoreChangeRecord) -> ChangeItem:
    return ChangeItem(
        **{
            field: getattr(value, field)
            for field in ChangeItem.model_fields
        }
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


def trace_result(value: CoreEvidenceTraceResult) -> EvidenceTraceResult:
    return EvidenceTraceResult(
        trace=(
            EvidenceTraceItem(
                **{
                    field: getattr(value.trace, field)
                    for field in EvidenceTraceItem.model_fields
                }
            )
            if value.trace is not None
            else None
        )
    )


def session_list_result(value: CoreSessionPage) -> SessionListResult:
    return SessionListResult(
        items=tuple(_session(item) for item in value.items),
        next_cursor=value.next_cursor,
    )


def session_summary_result(value: CoreSessionGetResult) -> SessionSummaryResult:
    return SessionSummaryResult(
        summary=_session(value.session) if value.session is not None else None
    )


def change_list_result(value: CoreChangePage) -> ChangeListResult:
    return ChangeListResult(
        items=tuple(_change(item) for item in value.items),
        next_cursor=value.next_cursor,
    )


def change_get_result(value: CoreChangeGetResult) -> ChangeGetResult:
    return ChangeGetResult(
        change=_change(value.change) if value.change is not None else None
    )


__all__ = [
    "ChangeGetResult",
    "ChangeItem",
    "ChangeListResult",
    "EvidenceGetResult",
    "EvidenceHit",
    "EvidenceItem",
    "EvidenceResolveResult",
    "EvidenceSearchResult",
    "EvidenceTraceItem",
    "EvidenceTraceResult",
    "KnowledgeKind",
    "SessionListResult",
    "SessionSummaryItem",
    "SessionSummaryResult",
]
