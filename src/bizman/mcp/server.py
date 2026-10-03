from __future__ import annotations

from typing import Annotated, NoReturn

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from bizman.core import (
    AssetError,
    ConfigurationError,
    ContractMismatchError,
    CoreContext,
    ChangeGetRequest,
    ChangeListRequest,
    CurrentCompanyListRequest,
    CurrentProductListRequest,
    CurrentStatusRequest,
    CurrentUnitListRequest,
    DataIntegrityError,
    EvidenceTraceRequest,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    OperationError,
    SessionAnomalyListRequest,
    SessionCompareRequest,
    SessionGetRequest,
    SessionListRequest,
    current_status,
    get_change,
    get_knowledge,
    compare_sessions,
    get_session,
    list_changes,
    list_current_companies,
    list_current_products,
    list_current_units,
    list_session_anomalies,
    list_sessions,
    resolve_knowledge,
    search_knowledge,
    trace_evidence,
)
from bizman.mcp.models import (
    ChangeGetResult,
    ChangeListResult,
    CurrentCompanyListResult,
    CurrentProductListResult,
    CurrentStatusResult,
    CurrentUnitListResult,
    EvidenceGetResult,
    EvidenceResolveResult,
    EvidenceSearchResult,
    EvidenceTraceResult,
    KnowledgeKind,
    SessionAnomalyListResult,
    SessionCompareResult,
    SessionListResult,
    SessionSummaryResult,
    change_get_result,
    change_list_result,
    current_company_list_result,
    current_product_list_result,
    current_status_result,
    current_unit_list_result,
    get_result,
    resolve_result,
    search_result,
    session_anomaly_list_result,
    session_compare_result,
    session_list_result,
    trace_result,
    session_summary_result,
)


Query = Annotated[str, Field(min_length=1, max_length=512)]
Reference = Annotated[str, Field(min_length=1, max_length=256)]
SearchLimit = Annotated[int, Field(ge=1, le=50)]
PageLimit = Annotated[int, Field(ge=1, le=50)]
KnowledgeKinds = Annotated[list[KnowledgeKind], Field(max_length=9)]
SessionId = Annotated[
    str,
    Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
]
ProfileSha256 = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
ChangeId = Annotated[str, Field(min_length=1, max_length=256)]
Cursor = Annotated[str, Field(min_length=1, max_length=2048)]
EntityId = Annotated[
    str,
    Field(min_length=1, max_length=32, pattern=r"^[1-9][0-9]*$"),
]
EvidenceReference = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=r"^[a-z0-9][a-z0-9.-]*#(?:entry|seq)-(?:0|[1-9][0-9]*)$",
    ),
]

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


def _raise_tool_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AssetError):
        raise ToolError(
            "BizMan provenance or repository assets are unavailable or invalid."
        ) from exc
    if isinstance(exc, ConfigurationError):
        raise ToolError(
            "Agent Index is unavailable; rebuild it before using BizMan read tools."
        ) from exc
    if isinstance(exc, ContractMismatchError):
        raise ToolError(
            "Agent Index is incompatible with this BizMan version; rebuild it."
        ) from exc
    if isinstance(exc, DataIntegrityError):
        raise ToolError(
            "BizMan read data failed integrity validation; restore trusted derived assets."
        ) from exc
    if isinstance(exc, OperationError):
        raise ToolError("Agent Index read operation failed.") from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise ToolError("Invalid BizMan read request.") from exc
    raise AssertionError("unexpected exception passed to _raise_tool_error")


def _raise_current_tool_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AssetError):
        raise ToolError(
            "BizMan provenance or repository assets are unavailable or invalid."
        ) from exc
    if isinstance(exc, ConfigurationError):
        raise ToolError(
            "Current State is unavailable; rebuild it before using Current State read tools."
        ) from exc
    if isinstance(exc, ContractMismatchError):
        raise ToolError(
            "Current State is incompatible with this BizMan version; rebuild it."
        ) from exc
    if isinstance(exc, DataIntegrityError):
        raise ToolError(
            "Current State read data failed integrity validation; rebuild trusted "
            "Current State."
        ) from exc
    if isinstance(exc, OperationError):
        raise ToolError("Current State read operation failed.") from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise ToolError("Invalid Current State read request.") from exc
    raise AssertionError(
        "unexpected exception passed to _raise_current_tool_error"
    )


def build_server(context: CoreContext) -> MCPServer:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")

    server = MCPServer(
        "BizMan",
        instructions=(
            "Read-only BizMan evidence, provenance, session, change, and Current "
            "State tools. Use canonical refs returned by resolve/search for exact "
            "evidence gets, and evidence refs for provenance traces. Tool outputs "
            "come from BizMan Core."
        ),
    )

    @server.tool(
        name="evidence.resolve",
        title="Resolve BizMan evidence",
        description=(
            "Resolve one BizMan knowledge object by canonical ref, alias, title, "
            "or deterministic lexical search."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def evidence_resolve(
        query: Query,
        kinds: KnowledgeKinds | None = None,
    ) -> EvidenceResolveResult:
        try:
            value = resolve_knowledge(
                context,
                KnowledgeResolveRequest(
                    text=query,
                    kinds=tuple(kinds or ()),
                ),
            )
            return resolve_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="evidence.search",
        title="Search BizMan evidence",
        description=(
            "Search BizMan knowledge with deterministic exact/FTS retrieval. "
            "Returns bounded canonical hits and evidence references."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def evidence_search(
        query: Query,
        limit: SearchLimit = 10,
        kinds: KnowledgeKinds | None = None,
    ) -> EvidenceSearchResult:
        try:
            value = search_knowledge(
                context,
                KnowledgeSearchRequest(
                    text=query,
                    limit=limit,
                    kinds=tuple(kinds or ()),
                ),
            )
            return search_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="sessions.list",
        title="List BizMan sessions",
        description=(
            "List bounded verified BizMan session summaries using an opaque Core cursor."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def sessions_list(
        limit: PageLimit = 10,
        cursor: Cursor | None = None,
    ) -> SessionListResult:
        try:
            value = list_sessions(
                context,
                SessionListRequest(limit=limit, cursor=cursor),
            )
            return session_list_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="sessions.summary",
        title="Get BizMan session summary",
        description="Fetch one verified BizMan session summary by canonical UUIDv7.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def sessions_summary(session_id: SessionId) -> SessionSummaryResult:
        try:
            value = get_session(
                context,
                SessionGetRequest(session_id=session_id),
            )
            return session_summary_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="sessions.compare",
        title="Compare BizMan session summaries",
        description=(
            "Compare two verified session summaries using deterministic signed "
            "deltas where every numeric delta is to_session minus from_session."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def sessions_compare(
        from_session_id: SessionId,
        to_session_id: SessionId,
    ) -> SessionCompareResult:
        try:
            return session_compare_result(
                compare_sessions(
                    context,
                    SessionCompareRequest(
                        from_session_id=from_session_id,
                        to_session_id=to_session_id,
                    ),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="sessions.anomalies",
        title="List BizMan session anomaly signals",
        description=(
            "List sessions with observed warnings, normalized anomalies, or "
            "uncorrelated actions. No severity score or causal inference is applied."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def sessions_anomalies(
        limit: PageLimit = 10,
        cursor: Cursor | None = None,
    ) -> SessionAnomalyListResult:
        try:
            return session_anomaly_list_result(
                list_session_anomalies(
                    context,
                    SessionAnomalyListRequest(
                        limit=limit,
                        cursor=cursor,
                    ),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="changes.list",
        title="List BizMan changes",
        description=(
            "List bounded deterministic change records, optionally scoped to one "
            "analysis profile, using an opaque Core cursor."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def changes_list(
        limit: PageLimit = 10,
        analysis_profile_sha256: ProfileSha256 | None = None,
        cursor: Cursor | None = None,
    ) -> ChangeListResult:
        try:
            value = list_changes(
                context,
                ChangeListRequest(
                    limit=limit,
                    analysis_profile_sha256=analysis_profile_sha256,
                    cursor=cursor,
                ),
            )
            return change_list_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="changes.get",
        title="Get BizMan change",
        description=(
            "Fetch one deterministic change record by analysis profile and change identity."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def changes_get(
        analysis_profile_sha256: ProfileSha256,
        change_id: ChangeId,
    ) -> ChangeGetResult:
        try:
            value = get_change(
                context,
                ChangeGetRequest(
                    analysis_profile_sha256=analysis_profile_sha256,
                    change_id=change_id,
                ),
            )
            return change_get_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="evidence.trace",
        title="Trace BizMan evidence provenance",
        description=(
            "Resolve one BizMan evidence reference to bounded verified source metadata. "
            "Raw HAR/session bytes are never returned."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def evidence_trace(evidence_ref: EvidenceReference) -> EvidenceTraceResult:
        try:
            return trace_result(
                trace_evidence(
                    context,
                    EvidenceTraceRequest(evidence_ref=evidence_ref),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="evidence.get",
        title="Get BizMan evidence",
        description=(
            "Fetch one BizMan knowledge item by canonical ref with aliases, body, "
            "source dataset, and bounded evidence references."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def evidence_get(ref: Reference) -> EvidenceGetResult:
        try:
            value = get_knowledge(context, KnowledgeGetRequest(ref=ref))
            return get_result(value)
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    @server.tool(
        name="current.status",
        title="Get BizMan Current State status",
        description=(
            "Return bounded Current State projection metadata: identity, input/state "
            "fingerprints, ready/stale status, and replay checkpoint."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def current_status_tool() -> CurrentStatusResult:
        try:
            return current_status_result(
                current_status(context, CurrentStatusRequest())
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_current_tool_error(exc)

    @server.tool(
        name="current.companies",
        title="List BizMan Current State companies",
        description=(
            "List bounded Current State companies using an opaque Core cursor bound "
            "to the state fingerprint."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def current_companies(
        limit: PageLimit = 10,
        cursor: Cursor | None = None,
    ) -> CurrentCompanyListResult:
        try:
            return current_company_list_result(
                list_current_companies(
                    context,
                    CurrentCompanyListRequest(limit=limit, cursor=cursor),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_current_tool_error(exc)

    @server.tool(
        name="current.units",
        title="List BizMan Current State units",
        description=(
            "List bounded Current State units, optionally scoped to one company, "
            "using an opaque Core cursor bound to the filter and state fingerprint."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def current_units(
        limit: PageLimit = 10,
        cursor: Cursor | None = None,
        company_id: EntityId | None = None,
    ) -> CurrentUnitListResult:
        try:
            return current_unit_list_result(
                list_current_units(
                    context,
                    CurrentUnitListRequest(
                        limit=limit,
                        cursor=cursor,
                        company_id=company_id,
                    ),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_current_tool_error(exc)

    @server.tool(
        name="current.products",
        title="List BizMan Current State products",
        description=(
            "List bounded Current State unit products, optionally scoped to one unit, "
            "using an opaque Core cursor bound to the filter and state fingerprint."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def current_products(
        limit: PageLimit = 10,
        cursor: Cursor | None = None,
        unit_id: EntityId | None = None,
    ) -> CurrentProductListResult:
        try:
            return current_product_list_result(
                list_current_products(
                    context,
                    CurrentProductListRequest(
                        limit=limit,
                        cursor=cursor,
                        unit_id=unit_id,
                    ),
                )
            )
        except (
            AssetError,
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_current_tool_error(exc)

    return server


__all__ = ["build_server"]
