from __future__ import annotations

from datetime import UTC, datetime
import secrets
import time
from typing import Any
import uuid


def _uuid7_fallback() -> uuid.UUID:
    """Generate an RFC 9562 UUIDv7 for Python versions before 3.14.

    Python 3.14+ uses the stdlib implementation. The fallback is only for
    compatibility with existing developer environments and does not promise
    monotonic ordering for multiple IDs generated within the same millisecond.
    """
    unix_ms = time.time_ns() // 1_000_000
    if unix_ms >= 1 << 48:
        raise OverflowError("Unix timestamp does not fit UUIDv7 48-bit field")
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        (unix_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)


def new_uuid7() -> str:
    generator = getattr(uuid, "uuid7", None)
    if generator is not None:
        return str(generator())
    return str(_uuid7_fallback())


def new_session_manifest(
    *,
    collector_version: str,
    browser_product: str,
    browser_version: str,
    protocol_version: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "session_id": new_uuid7(),
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ended_at": None,
        "status": "running",
        "collector": {"name": "bizman-cdp", "version": collector_version},
        "browser": {"product": browser_product, "version": browser_version},
        "protocol": {
            "name": "cdp",
            "version": protocol_version,
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [],
        "artifact_count": 0,
        "warnings": [],
    }
