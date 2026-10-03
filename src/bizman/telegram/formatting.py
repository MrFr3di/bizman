"""Deterministic bounded formatting of BizMan Core read results for Telegram.

Messages are plain text (no parse mode) so externally derived knowledge text
can never interact with Telegram markup parsing. Every exported formatter is
pure: identical Core inputs produce identical bytes.
"""

from __future__ import annotations

from bizman.core import (
    ChangePage,
    ChangeRecord,
    CurrentProductPage,
    CurrentStatusResult,
    CurrentUnitPage,
    EvidenceTrace,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeSearchResult,
    PlanResult,
    PlanRow,
    SessionAnomalyPage,
    SessionComparison,
    SessionGetResult,
    SessionPage,
    SessionRecord,
)

MAX_MESSAGE_CHARS = 4000
_MAX_LONG_TEXT_CHARS = 1200
_MAX_LABEL_CHARS = 160
_MAX_EVIDENCE_REFS = 8
_LIST_LIMIT = 10


def trim(text: str, *, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Bound one Telegram message, cutting on the last line boundary.

    The total result, including the truncation suffix, never exceeds ``limit``.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if limit <= 64:
        raise ValueError("limit must leave room for the truncation suffix")
    if len(text) <= limit:
        return text
    cut = limit - 48
    newline = text.rfind("\n", 0, cut)
    if newline > cut // 2:
        cut = newline
    omitted = len(text) - cut
    return text[:cut].rstrip() + f"\n… (+{omitted} chars truncated)"


def _session_line(record: SessionRecord, *, index: int) -> str:
    return (
        f"{index}) {record.session_id} | {record.started_at} | "
        f"events {record.event_count} | actions {record.action_count} | "
        f"warnings {record.warning_count} | anomalies {record.anomaly_count}"
    )


def format_status(result: SessionPage) -> str:
    if not result.items:
        return "Agent Index: OK\nNo finalized sessions recorded yet."
    latest = result.items[0]
    return (
        "Agent Index: OK\nLatest finalized session:\n"
        + _session_line(latest, index=1)
    )


def format_sessions(page: SessionPage) -> str:
    if not page.items:
        return "No finalized sessions recorded yet."
    lines = ["Sessions (latest 10):"]
    lines.extend(_session_line(record, index=index) for index, record in enumerate(page.items, start=1))
    if page.next_cursor is not None:
        lines.append("… more sessions exist; the Telegram adapter shows the first page only.")
    return "\n".join(lines)


def format_session(result: SessionGetResult) -> str:
    if result.session is None:
        return "Session not found."
    record = result.session
    return "\n".join(
        (
            f"Session {record.session_id}",
            f"status: {record.status}",
            f"started: {record.started_at}",
            f"ended: {record.ended_at}",
            f"events: {record.event_count} (HTTP requests {record.http_request_count}, "
            f"responses {record.http_response_count})",
            f"actions: {record.action_count}",
            "correlations: strong "
            f"{record.correlation_strong_count}, probable {record.correlation_probable_count}, "
            f"temporal {record.correlation_temporal_count}, exact {record.correlation_exact_count}",
            f"uncorrelated actions: {record.uncorrelated_action_count}",
            f"warnings: {record.warning_count}, anomalies: {record.anomaly_count}",
        )
    )


def format_comparison(comparison: SessionComparison) -> str:
    signed = lambda value: f"{value:+d}"  # noqa: E731
    return "\n".join(
        (
            "Session comparison (delta = to - from)",
            f"from: {comparison.from_session_id} {comparison.from_started_at} ({comparison.from_status})",
            f"to:   {comparison.to_session_id} {comparison.to_started_at} ({comparison.to_status})",
            f"events {signed(comparison.event_count_delta)} | actions "
            f"{signed(comparison.action_count_delta)} | requests "
            f"{signed(comparison.http_request_count_delta)} | responses "
            f"{signed(comparison.http_response_count_delta)}",
            "correlations: strong "
            f"{signed(comparison.correlation_strong_count_delta)}, probable "
            f"{signed(comparison.correlation_probable_count_delta)}, temporal "
            f"{signed(comparison.correlation_temporal_count_delta)}, exact "
            f"{signed(comparison.correlation_exact_count_delta)}",
            f"uncorrelated {signed(comparison.uncorrelated_action_count_delta)} | "
            f"warnings {signed(comparison.warning_count_delta)} | "
            f"anomalies {signed(comparison.anomaly_count_delta)}",
        )
    )


def format_anomalies(page: SessionAnomalyPage) -> str:
    if not page.items:
        return "No sessions with warning/anomaly/uncorrelated-action signals."
    lines = ["Sessions with signals (latest 10):"]
    for index, record in enumerate(page.items, start=1):
        lines.append(
            f"{index}) {record.session_id} | {record.started_at} | status {record.status}"
        )
        lines.append(
            f"   warnings {record.warning_count} | anomalies {record.anomaly_count} | "
            f"uncorrelated {record.uncorrelated_action_count}"
        )
    if page.next_cursor is not None:
        lines.append("… more pages exist; the Telegram adapter shows the first page only.")
    return "\n".join(lines)


def _profile_label(profile: str) -> str:
    return profile if len(profile) != 64 else profile[:12] + "…"


def _change_lines(record: ChangeRecord, *, index: int) -> list[str]:
    return [
        f"{index}) {record.change_id}",
        f"   profile {_profile_label(record.analysis_profile_sha256)}",
        f"   rule {record.rule_id} v{record.rule_version} | {record.kind} | {record.novelty_class}",
        f"   first {record.first_seen_at} in {record.first_session_id}",
        f"   last {record.last_seen_at} in {record.last_session_id} | occurrences "
        f"{record.occurrence_count}",
    ]


def format_changes(page: ChangePage) -> str:
    if not page.items:
        return "No changes recorded."
    lines = [f"Changes (first {_LIST_LIMIT}):"]
    for index, record in enumerate(page.items, start=1):
        lines.extend(_change_lines(record, index=index))
    if page.next_cursor is not None:
        lines.append("… more pages exist; the Telegram adapter shows the first page only.")
    return "\n".join(lines)


def format_change(record: ChangeRecord) -> str:
    lines = _change_lines(record, index=1)
    lines[0] = f"Change {record.change_id}"
    lines[1] = f"profile {record.analysis_profile_sha256}"
    return "\n".join(lines)


def _bounded_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _evidence_refs_line(refs: tuple[str, ...]) -> str:
    shown = refs[:_MAX_EVIDENCE_REFS]
    suffix = f" (+{len(refs) - len(shown)} more)" if len(refs) > len(shown) else ""
    if not shown:
        return "Evidence refs: none"
    return "Evidence refs: " + ", ".join(shown) + suffix


def format_search(query: str, result: KnowledgeSearchResult) -> str:
    if not result.items:
        return f"No knowledge matched \"{query}\"."
    lines = [f"Knowledge search \"{query}\" (top {len(result.items)}):"]
    for index, hit in enumerate(result.items, start=1):
        lines.append(f"{index}) [{hit.kind}] {hit.ref} — {hit.title} ({hit.match_kind})")
        lines.append(f"   evidence: {', '.join(hit.evidence_refs) or 'none'}")
    return "\n".join(lines)


def format_knowledge_item(item: KnowledgeItem) -> str:
    return "\n".join(
        (
            f"[{item.ref}] kind={item.kind}",
            f"Title: {item.title}",
            f"Aliases: {', '.join(item.aliases) if item.aliases else 'none'}",
            f"Source dataset: {item.source_dataset}",
            f"Body:\n{_bounded_text(item.body, limit=_MAX_LONG_TEXT_CHARS)}",
            _evidence_refs_line(item.evidence_refs),
        )
    )


def format_trace(trace: EvidenceTrace) -> str:
    lines = (
        f"Evidence trace: {trace.evidence_ref}",
        f"source: {trace.source_id} ({trace.source_kind})",
        f"locator: {trace.locator_kind} {trace.ordinal} of {trace.source_record_count} records",
        f"observed: {trace.observed_from}"
        + (f" .. {trace.observed_to}" if trace.observed_to else ""),
        f"raw source committed: {'yes' if trace.raw_source_committed else 'no'}",
        f"privacy: {_bounded_text(trace.privacy, limit=_MAX_LABEL_CHARS)}",
        f"provenance policy: {_bounded_text(trace.provenance_policy, limit=_MAX_LABEL_CHARS)}",
    )
    if trace.runtime_session_id is not None:
        lines = (*lines, f"runtime session: {trace.runtime_session_id}")
    return "\n".join(lines)


def format_hit(hit: KnowledgeHit) -> str:
    return (
        f"[{hit.kind}] {hit.ref} — {hit.title} ({hit.match_kind})\n"
        + _evidence_refs_line(hit.evidence_refs)
    )


def _fingerprint_label(value: str) -> str:
    return value if len(value) != 64 else value[:12] + "…"


def _quality_label(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _price_label(value: float | None) -> str:
    return "UNKNOWN" if value is None else _quality_label(value)


def format_current_status(result: CurrentStatusResult) -> str:
    lines = [
        f"Current State: {result.status}",
        f"projection: {result.projection_name} v{result.projection_version}",
        f"state fingerprint: {_fingerprint_label(result.state_fingerprint)}",
        f"input fingerprint: {_fingerprint_label(result.input_fingerprint)}",
        f"sessions replayed: {result.session_count}",
    ]
    if result.last_session_id is not None:
        last = result.last_session_id
        if result.last_sequence is not None:
            last += f" (last sequence {result.last_sequence})"
        lines.append(f"last session: {last}")
    if result.stale_reason is not None:
        lines.append(
            "stale reason: "
            + _bounded_text(result.stale_reason, limit=_MAX_LABEL_CHARS)
        )
    return "\n".join(lines)


def format_units(page: CurrentUnitPage) -> str:
    if not page.items:
        return "No Current State units recorded."
    lines = ["Current State units (first 10):"]
    for index, record in enumerate(page.items, start=1):
        lines.append(
            f"{index}) {record.unit_id} — {record.display_name} "
            f"({record.city_name}, company {record.company_id}, "
            f"level {record.level})"
        )
        lines.append(
            f"   observed {record.observed_at} | evidence "
            f"{record.source_session_id}#seq-{record.source_sequence}"
        )
    if page.next_cursor is not None:
        lines.append("… more units exist; the Telegram adapter shows the first page only.")
    return "\n".join(lines)


def format_products(page: CurrentProductPage, *, unit_id: str | None = None) -> str:
    if not page.items:
        if unit_id is not None:
            return f"Unit {unit_id}: no observed products."
        return "No Current State unit products recorded."
    heading = (
        f"Unit {unit_id} products (first {_LIST_LIMIT}):"
        if unit_id is not None
        else "Current State unit products (first 10):"
    )
    lines = [heading]
    for index, record in enumerate(page.items, start=1):
        lines.append(
            f"{index}) unit {record.unit_id} product {record.product_numeric_id} "
            f"| stock {record.stock_qty} | sales {record.sales_volume} "
            f"| our {record.our_price} / city {record.city_price} "
            f"| quality {_quality_label(record.stock_quality)}/"
            f"{_quality_label(record.city_quality)} "
            f"| supply {record.supply_qty} @ {record.supply_cost}"
        )
        lines.append(
            f"   observed {record.observed_at} | evidence "
            f"{record.source_session_id}#seq-{record.source_sequence}"
        )
    if page.next_cursor is not None:
        lines.append(
            "… more unit products exist; the Telegram adapter shows the "
            "first page only."
        )
    return "\n".join(lines)


def _plan_row_lines(row: PlanRow, *, index: int) -> list[str]:
    return [
        f"{index}) unit {row.unit_id} product {row.product_numeric_id} "
        f"| order {row.order_qty} "
        f"| recommended {_price_label(row.recommended_price)} "
        f"| reference {_price_label(row.reference_price)} "
        f"| flags [{', '.join(row.flags) or 'none'}]",
        f"   sales {row.sales_volume} | stock {row.stock_qty} + supply "
        f"{row.supply_qty} -> target {row.target_stock} "
        f"| our {row.our_price} / city {row.city_price} "
        f"| supply cost {row.supply_cost}",
        f"   observed {row.observed_at} | evidence "
        f"{', '.join(row.evidence_refs)}",
    ]


def format_plan(result: PlanResult) -> str:
    lines = [
        f"Plan (weeks {result.weeks}) from state "
        f"{_fingerprint_label(result.generated_from_state_fingerprint)}:"
    ]
    if result.rows:
        lines.append(f"Recommendation rows ({len(result.rows)}):")
        for index, row in enumerate(result.rows, start=1):
            lines.extend(_plan_row_lines(row, index=index))
    else:
        lines.append("No recommendation rows on a ready goods surface.")
    if result.excluded_surfaces:
        lines.append("Not recommended (goods surface not ready):")
        for exclusion in result.excluded_surfaces:
            reason = ""
            if exclusion.stale_reason is not None:
                reason = " — " + _bounded_text(
                    exclusion.stale_reason, limit=_MAX_LABEL_CHARS
                )
            lines.append(
                f"- unit {exclusion.unit_id} ({exclusion.surface}): "
                f"{exclusion.status}{reason}"
            )
    lines.append("Sources: " + ", ".join(result.source_refs))
    return "\n".join(lines)


__all__ = [
    "MAX_MESSAGE_CHARS",
    "format_anomalies",
    "format_change",
    "format_changes",
    "format_comparison",
    "format_current_status",
    "format_hit",
    "format_knowledge_item",
    "format_plan",
    "format_products",
    "format_search",
    "format_session",
    "format_sessions",
    "format_status",
    "format_trace",
    "format_units",
    "trim",
]
