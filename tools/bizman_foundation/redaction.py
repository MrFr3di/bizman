from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


_DEFAULT_DROP_HEADERS = frozenset(
    {
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
    }
)

_DEFAULT_DROP_FIELD_PATTERNS = (
    re.compile(r"(?:^|[_-])(password|passwd|pwd)(?:$|[_-])", re.IGNORECASE),
    re.compile(r"(?:^|[_-])(token|secret|session)(?:$|[_-])", re.IGNORECASE),
    re.compile(r"csrf", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"cookie", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    drop_headers: frozenset[str]
    drop_field_patterns: tuple[re.Pattern[str], ...]
    first_party_only: bool = True
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 2_097_152

    @classmethod
    def default(cls) -> "RedactionPolicy":
        return cls(
            drop_headers=_DEFAULT_DROP_HEADERS,
            drop_field_patterns=_DEFAULT_DROP_FIELD_PATTERNS,
            first_party_only=True,
            max_request_bytes=1_048_576,
            max_response_bytes=2_097_152,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RedactionPolicy":
        headers = value.get("headers", {})
        fields = value.get("fields", {})
        drop_headers = headers.get("drop", []) if isinstance(headers, Mapping) else []
        drop_patterns = (
            fields.get("drop_patterns", []) if isinstance(fields, Mapping) else []
        )
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

        return cls(
            drop_headers=frozenset(item.casefold() for item in drop_headers),
            drop_field_patterns=tuple(re.compile(item) for item in drop_patterns),
            first_party_only=first_party_only,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )

    def should_drop_field(self, name: str) -> bool:
        return any(pattern.search(name) for pattern in self.drop_field_patterns)


def redact_headers(
    headers: Mapping[str, Any], policy: RedactionPolicy
) -> dict[str, Any]:
    return {
        name: value
        for name, value in headers.items()
        if name.casefold() not in policy.drop_headers
    }


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
