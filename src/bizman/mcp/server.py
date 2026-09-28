from __future__ import annotations

from typing import Annotated, NoReturn

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from bizman.core import (
    ConfigurationError,
    ContractMismatchError,
    CoreContext,
    ChangeGetRequest,
    ChangeListRequest,
    DataIntegrityError,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    OperationError,
    SessionGetRequest,
    SessionListRequest,
    get_change,
    get_knowledge,
    get_session,
    list_changes,
    list_sessions,
    resolve_knowledge,
    search_knowledge,
)
from bizman.mcp.models import (
    ChangeGetResult,
    ChangeListResult,
    EvidenceGetResult,
    EvidenceResolveResult,
    EvidenceSearchResult,
    KnowledgeKind,
    SessionListResult,
    SessionSummaryResult,
    change_get_result,
    change_list_result,
    get_result,
    resolve_result,
    search_result,
    session_list_result,
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

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


def _raise_tool_error(exc: Exception) -> NoReturn:
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
            "Agent Index failed integrity validation; rebuild it from trusted inputs."
        ) from exc
    if isinstance(exc, OperationError):
        raise ToolError("Agent Index read operation failed.") from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise ToolError("Invalid BizMan read request.") from exc
    raise AssertionError("unexpected exception passed to _raise_tool_error")


def build_server(context: CoreContext) -> MCPServer:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")

    server = MCPServer(
        "BizMan",
        instructions=(
            "Read-only BizMan evidence, session, and change tools. Use canonical refs "
            "returned by resolve/search for exact evidence gets. Tool outputs come from "
            "the deterministic BizMan Core read model."
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
            ConfigurationError,
            ContractMismatchError,
            DataIntegrityError,
            OperationError,
            TypeError,
            ValueError,
        ) as exc:
            _raise_tool_error(exc)

    return server


__all__ = ["build_server"]
