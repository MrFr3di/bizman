from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from bizman.core import (
    ConfigurationError,
    ContractMismatchError,
    CoreContext,
    DataIntegrityError,
    KnowledgeGetRequest,
    KnowledgeResolveRequest,
    KnowledgeSearchRequest,
    OperationError,
    get_knowledge,
    resolve_knowledge,
    search_knowledge,
)
from bizman.mcp.models import (
    EvidenceGetResult,
    EvidenceResolveResult,
    EvidenceSearchResult,
    KnowledgeKind,
    get_result,
    resolve_result,
    search_result,
)


Query = Annotated[str, Field(min_length=1, max_length=512)]
Reference = Annotated[str, Field(min_length=1, max_length=256)]
SearchLimit = Annotated[int, Field(ge=1, le=50)]
KnowledgeKinds = Annotated[list[KnowledgeKind], Field(max_length=9)]

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


def _raise_tool_error(exc: Exception) -> None:
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
            "Read-only BizMan evidence tools. Use canonical refs returned by resolve/search "
            "for exact gets. Tool outputs come from the deterministic BizMan Core read model."
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
            raise AssertionError("unreachable")

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
            raise AssertionError("unreachable")

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
            raise AssertionError("unreachable")

    return server


__all__ = ["build_server"]
