"""Canonical, bounded keyset-pagination cursors shared by Core read surfaces.

A cursor is an opaque ``bmcur1.`` token that wraps the canonical JSON payload
``{generation, kind, scope, key, version}`` together with a SHA-256 checksum of
that payload.  ``generation`` binds a page to the semantic snapshot it was read
from, ``kind``/``scope`` bind it to the query identity, and ``key`` carries the
last emitted ordering key.  The prefix and payload version are part of the wire
format and must not change without a new prefix version.

Sibling Core modules import the public helpers below; the algorithm itself is
kept private to this module so that every read surface emits byte-identical
tokens.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re


MAX_CURSOR_LENGTH = 2048
CURSOR_PREFIX = "bmcur1."
CURSOR_VERSION = 1
_MAX_CURSOR_KEY_COMPONENTS = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encode_cursor(
    *,
    kind: str,
    scope: str,
    generation: str,
    key: tuple[str, ...],
) -> str:
    """Encode one canonical opaque cursor for a bounded Core page."""

    if _SHA256_RE.fullmatch(generation) is None:
        raise ValueError("cursor generation must be a lowercase SHA-256")
    payload = {
        "generation": generation,
        "kind": kind,
        "scope": scope,
        "key": list(key),
        "version": CURSOR_VERSION,
    }
    payload_bytes = _canonical_json(payload)
    envelope = {
        "payload": payload,
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    token = base64.urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")
    return CURSOR_PREFIX + token


def decode_cursor(cursor: str, *, kind: str, scope: str) -> tuple[str, ...]:
    """Validate one cursor against a query identity and return ``(generation, *key)``."""

    _require_text(cursor, name="cursor")
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise ValueError(f"cursor exceeds {MAX_CURSOR_LENGTH} characters")
    if not cursor.startswith(CURSOR_PREFIX):
        raise ValueError("cursor has an unsupported format")
    encoded = cursor[len(CURSOR_PREFIX) :]
    if not encoded:
        raise ValueError("cursor payload is empty")
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(
            (encoded + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is malformed") from exc
    canonical_encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if not hmac.compare_digest(encoded, canonical_encoded):
        raise ValueError("cursor encoding is not canonical")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise ValueError("cursor envelope is invalid")
    payload = envelope["payload"]
    checksum = envelope["sha256"]
    if (
        not isinstance(payload, dict)
        or set(payload) != {"generation", "kind", "scope", "key", "version"}
        or not isinstance(checksum, str)
        or _SHA256_RE.fullmatch(checksum) is None
    ):
        raise ValueError("cursor envelope is invalid")
    expected = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if not hmac.compare_digest(checksum, expected):
        raise ValueError("cursor integrity check failed")
    if payload["version"] != CURSOR_VERSION:
        raise ValueError("cursor version is unsupported")
    if payload["kind"] != kind or payload["scope"] != scope:
        raise ValueError("cursor does not belong to this query")
    generation = payload["generation"]
    if not isinstance(generation, str) or _SHA256_RE.fullmatch(generation) is None:
        raise ValueError("cursor generation is invalid")
    key = payload["key"]
    if (
        not isinstance(key, list)
        or not 1 <= len(key) <= _MAX_CURSOR_KEY_COMPONENTS
        or not all(isinstance(value, str) and value for value in key)
    ):
        raise ValueError("cursor key is invalid")
    return (generation, *key)


__all__ = [
    "CURSOR_PREFIX",
    "CURSOR_VERSION",
    "MAX_CURSOR_LENGTH",
    "decode_cursor",
    "encode_cursor",
]
