"""Pure Telegram command handlers over the stable BizMan Core boundary.

This module is transport-agnostic and must not import aiogram. ``execute``
never raises for expected failures: argument errors, Core errors and unknown
commands are translated into sanitized plain-text replies. It never includes
filesystem paths, tokens or raw evidence bytes in any reply.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from bizman.core import (
    AssetError,
    BizManError,
    ChangeGetRequest,
    ChangeListRequest,
    ConfigurationError,
    ContractMismatchError,
    CoreContext,
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
    compare_sessions,
    get_change,
    get_knowledge,
    get_session,
    list_changes,
    list_session_anomalies,
    list_sessions,
    resolve_knowledge,
    search_knowledge,
    trace_evidence,
)
from bizman.telegram import formatting as fmt

_LIST_LIMIT = 10
_SEARCH_LIMIT = 5
_MAX_QUERY_CHARS = 512
_MAX_REF_CHARS = 256

_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROFILE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*#(?:entry|seq)-(?:0|[1-9][0-9]*)$")
_REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")

_ERROR_REPLIES: tuple[tuple[type[BaseException], str], ...] = (
    (
        AssetError,
        "BizMan repository assets are unavailable or invalid.",
    ),
    (
        ConfigurationError,
        "Agent Index is unavailable; rebuild it before using BizMan commands.",
    ),
    (
        ContractMismatchError,
        "Agent Index is incompatible with this BizMan version; rebuild it.",
    ),
    (
        DataIntegrityError,
        "BizMan read data failed integrity validation; restore trusted derived assets.",
    ),
    (
        OperationError,
        "Agent Index read operation failed.",
    ),
)

_HELP_TEXT = "\n".join(
    (
        "BizMan read-only assistant. Commands:",
        "/help — this help",
        "/status — Agent Index health and latest session",
        "/sessions — latest finalized sessions",
        "/session <uuidv7> — one session summary",
        "/compare <uuidv7> <uuidv7> — deterministic session deltas",
        "/anomalies — sessions with warning/anomaly signals",
        "/changes [profile_sha256] — deterministic change records",
        "/change <profile_sha256> <change_id> — one change record",
        "/k <query> — search BizMan knowledge",
        "/kb <ref> — fetch one knowledge item by canonical ref",
        "/trace <evidence_ref> — bounded evidence provenance trace",
        "All data comes from verified BizMan evidence; unknown facts are reported as unknown.",
    )
)


@dataclass(frozen=True, slots=True)
class _UsageError(ValueError):
    usage: str


def _require_session_id(value: str) -> str:
    if _SESSION_ID_RE.fullmatch(value) is None:
        raise _UsageError("<uuidv7> must be a canonical lowercase UUIDv7")
    return value


def _require_profile(value: str) -> str:
    if _PROFILE_SHA256_RE.fullmatch(value) is None:
        raise _UsageError("<profile_sha256> must be 64 lowercase hexadecimal characters")
    return value


def _require_evidence_ref(value: str) -> str:
    if _EVIDENCE_REF_RE.fullmatch(value) is None:
        raise _UsageError(
            "<evidence_ref> must look like src.<capture>#entry-N or <source>#seq-N"
        )
    return value


def _require_ref(value: str) -> str:
    if _REF_RE.fullmatch(value) is None:
        raise _UsageError("<ref> must be a canonical BizMan knowledge ref")
    return value


def _help(context: CoreContext, rest: str) -> str:
    return _HELP_TEXT


def _status(context: CoreContext, rest: str) -> str:
    if rest:
        raise _UsageError("/status takes no arguments")
    return fmt.format_status(
        list_sessions(context, SessionListRequest(limit=1)),
    )


def _sessions(context: CoreContext, rest: str) -> str:
    if rest:
        raise _UsageError("/sessions takes no arguments")
    return fmt.format_sessions(
        list_sessions(context, SessionListRequest(limit=_LIST_LIMIT)),
    )


def _session(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) != 1:
        raise _UsageError("/session <uuidv7>")
    result = get_session(
        context,
        SessionGetRequest(session_id=_require_session_id(parts[0])),
    )
    return fmt.format_session(result)


def _compare(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) != 2:
        raise _UsageError("/compare <uuidv7> <uuidv7>")
    result = compare_sessions(
        context,
        SessionCompareRequest(
            from_session_id=_require_session_id(parts[0]),
            to_session_id=_require_session_id(parts[1]),
        ),
    )
    if result.comparison is None:
        missing = ", ".join(result.missing_session_ids)
        return f"Session comparison unavailable; missing sessions: {missing}"
    return fmt.format_comparison(result.comparison)


def _anomalies(context: CoreContext, rest: str) -> str:
    if rest:
        raise _UsageError("/anomalies takes no arguments")
    return fmt.format_anomalies(
        list_session_anomalies(
            context,
            SessionAnomalyListRequest(limit=_LIST_LIMIT),
        ),
    )


def _changes(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) > 1:
        raise _UsageError("/changes [profile_sha256]")
    profile = _require_profile(parts[0]) if parts else None
    return fmt.format_changes(
        list_changes(
            context,
            ChangeListRequest(limit=_LIST_LIMIT, analysis_profile_sha256=profile),
        ),
    )


def _change(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) != 2:
        raise _UsageError("/change <profile_sha256> <change_id>")
    result = get_change(
        context,
        ChangeGetRequest(
            analysis_profile_sha256=_require_profile(parts[0]),
            change_id=parts[1],
        ),
    )
    if result.change is None:
        return "Change not found for this profile and change id."
    return fmt.format_change(result.change)


def _knowledge_search(context: CoreContext, rest: str) -> str:
    query = rest.strip()
    if not query:
        raise _UsageError("/k <query>")
    if len(query) > _MAX_QUERY_CHARS:
        raise _UsageError(f"/k query must not exceed {_MAX_QUERY_CHARS} characters")
    resolved = resolve_knowledge(
        context,
        KnowledgeResolveRequest(text=query),
    )
    if resolved.hit is not None:
        return fmt.format_hit(resolved.hit)
    result = search_knowledge(
        context,
        KnowledgeSearchRequest(text=query, limit=_SEARCH_LIMIT),
    )
    return fmt.format_search(query, result)


def _knowledge_get(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) != 1:
        raise _UsageError("/kb <ref>")
    ref = _require_ref(parts[0])
    result = get_knowledge(context, KnowledgeGetRequest(ref=ref))
    if result.item is None:
        return f"Knowledge item not found: {ref}"
    return fmt.format_knowledge_item(result.item)


def _trace(context: CoreContext, rest: str) -> str:
    parts = rest.split()
    if len(parts) != 1:
        raise _UsageError("/trace <evidence_ref>")
    result = trace_evidence(
        context,
        EvidenceTraceRequest(evidence_ref=_require_evidence_ref(parts[0])),
    )
    if result.trace is None:
        return "Evidence reference not found in source provenance registries."
    return fmt.format_trace(result.trace)


_COMMANDS: dict[str, object] = {
    "help": _help,
    "status": _status,
    "sessions": _sessions,
    "session": _session,
    "compare": _compare,
    "anomalies": _anomalies,
    "changes": _changes,
    "change": _change,
    "k": _knowledge_search,
    "kb": _knowledge_get,
    "trace": _trace,
}


def _core_error_reply(exc: BizManError) -> str:
    for error_type, reply in _ERROR_REPLIES:
        if isinstance(exc, error_type):
            return reply
    return "BizMan operation failed."


def execute(context: CoreContext, message_text: str) -> str:
    """Dispatch one Telegram command line to a Core read use case."""
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(message_text, str):
        raise TypeError("message_text must be a string")
    parts = message_text.strip().split(maxsplit=1)
    if not parts:
        return _HELP_TEXT
    command = parts[0].lstrip("/").split("@", maxsplit=1)[0].lower()
    handler = _COMMANDS.get(command)
    if handler is None:
        return f"Unknown command: {command}\n\n{_HELP_TEXT}"
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        reply = handler(context, rest)
    except _UsageError as exc:
        return f"Invalid arguments.\n\nUsage: {exc.usage}"
    except (TypeError, ValueError):
        return "Invalid request."
    except BizManError as exc:
        return _core_error_reply(exc)
    except Exception:
        return "Unexpected BizMan error; check the bot log."
    return fmt.trim(reply)


__all__ = ["execute"]
