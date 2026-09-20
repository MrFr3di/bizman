from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


_DEFAULT_DROP_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
    "sec-websocket-key",
    "sec-websocket-accept",
    "sec-websocket-protocol",
    # URL-bearing headers can embed query credentials or session tokens. The
    # normalized event already records safe request/redirect paths separately.
    "referer",
    "referrer",
    "location",
    "content-location",
    "link",
    "refresh",
})

_DEFAULT_DROP_FIELD_PATTERNS = (
    re.compile(
        r"(?:password|passwd|pwd|token|secret|session|csrf|authorization|cookie)",
        re.IGNORECASE,
    ),
)

_DEFAULT_MIME_ALLOWLIST = (
    "application/json",
    "application/x-www-form-urlencoded",
    "text/html",
    "text/plain",
    "text/javascript",
    "application/javascript",
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    drop_headers: frozenset[str]
    drop_field_patterns: tuple[re.Pattern[str], ...]
    first_party_only: bool = True
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 2_097_152
    mime_allowlist: tuple[str, ...] = _DEFAULT_MIME_ALLOWLIST

    @classmethod
    def default(cls) -> "RedactionPolicy":
        return cls(
            drop_headers=_DEFAULT_DROP_HEADERS,
            drop_field_patterns=_DEFAULT_DROP_FIELD_PATTERNS,
            first_party_only=True,
            max_request_bytes=1_048_576,
            max_response_bytes=2_097_152,
            mime_allowlist=_DEFAULT_MIME_ALLOWLIST,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RedactionPolicy":
        headers = value.get("headers", {})
        fields = value.get("fields", {})
        drop_headers = headers.get("drop", []) if isinstance(headers, Mapping) else []
        drop_patterns = fields.get("drop_patterns", []) if isinstance(fields, Mapping) else []
        if not isinstance(drop_headers, list) or not all(
            isinstance(item, str) for item in drop_headers
        ):
            raise ValueError("redaction headers.drop must be a list of strings")
        if not isinstance(drop_patterns, list) or not all(
            isinstance(item, str) for item in drop_patterns
        ):
            raise ValueError("redaction fields.drop_patterns must be a list of strings")

        bodies = value.get("bodies", {})
        if not isinstance(bodies, Mapping):
            raise ValueError("redaction bodies must be an object")
        first_party_only = bodies.get("first_party_only", True)
        max_request_bytes = bodies.get("max_request_bytes", 1_048_576)
        max_response_bytes = bodies.get("max_response_bytes", 2_097_152)
        mime_allowlist = bodies.get("mime_allowlist", list(_DEFAULT_MIME_ALLOWLIST))
        if not isinstance(first_party_only, bool):
            raise ValueError("redaction bodies.first_party_only must be boolean")
        if not isinstance(max_request_bytes, int) or max_request_bytes < 0:
            raise ValueError(
                "redaction bodies.max_request_bytes must be a non-negative integer"
            )
        if not isinstance(max_response_bytes, int) or max_response_bytes < 0:
            raise ValueError(
                "redaction bodies.max_response_bytes must be a non-negative integer"
            )
        if not isinstance(mime_allowlist, list) or not all(
            isinstance(item, str) for item in mime_allowlist
        ):
            raise ValueError("redaction bodies.mime_allowlist must be a list of strings")

        return cls(
            drop_headers=frozenset(item.casefold() for item in drop_headers),
            drop_field_patterns=tuple(re.compile(item) for item in drop_patterns),
            first_party_only=first_party_only,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            mime_allowlist=tuple(mime_allowlist),
        )

    def should_drop_field(self, name: str) -> bool:
        return any(pattern.search(name) for pattern in self.drop_field_patterns)


def redact_headers(
    headers: Mapping[str, Any], policy: RedactionPolicy
) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, value in headers.items():
        if not isinstance(name, str):
            continue
        if name.casefold() in policy.drop_headers or policy.should_drop_field(name):
            continue
        redacted[name] = value
    return redacted


def redact_mapping(
    value: Mapping[str, Any], policy: RedactionPolicy
) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, item in value.items():
        key = str(name)
        if policy.should_drop_field(key):
            continue
        redacted[key] = _redact_value(item, policy)
    return redacted


def _redact_value(value: Any, policy: RedactionPolicy) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value, policy)
    if isinstance(value, list):
        return [_redact_value(item, policy) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, policy) for item in value]
    return value


def load_redaction_policy(path: Path) -> RedactionPolicy:
    with path.open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, Mapping):
        raise ValueError("redaction policy must be a JSON object")
    return RedactionPolicy.from_mapping(value)
