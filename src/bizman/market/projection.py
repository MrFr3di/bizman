"""Deterministic latest-observation market projection.

The projection answers one question per ``(surface, request_key)`` pair: what is
the most recent stored observation? For every key the record with the greatest
``(captured_at, source_sequence, observation_id)`` triple wins. ``captured_at``
values are canonical UTC RFC3339 strings, so string order equals chronological
order; the remaining key parts make the selection total for a fixed multiset of
records. Because winner selection and the ``(surface, request_key)`` ordering
depend only on that multiset, the same records produce an identical
:class:`MarketProjection` (including an identical ``projection_fingerprint``) in
any input order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Final

from bizman.foundation.fingerprint import canonical_sha256
from bizman.market.model import MarketContractError
from bizman.market.store import SURFACES, MarketObservationRecord


PROJECTION_SCHEMA: Final[str] = "bizman.market-projection.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_uuid7(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _UUID7_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical UUIDv7")
    return value


def _require_surface(value: object) -> str:
    text = _require_text(value, name="surface")
    if text not in SURFACES:
        raise ValueError("surface must be a known market surface")
    return text


def _canonical_instant(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _non_negative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class MarketProjectionEntry:
    """One latest observation selected for a ``(surface, request_key)`` pair."""

    surface: str
    request_key: str
    observation_id: str
    captured_at: str
    artifact_sha256: str
    response_text_sha256: str
    evidence_ref: str
    payload_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _require_surface(self.surface))
        object.__setattr__(
            self,
            "request_key",
            _require_text(self.request_key, name="request_key"),
        )
        object.__setattr__(
            self,
            "observation_id",
            _require_uuid7(self.observation_id, name="observation_id"),
        )
        object.__setattr__(
            self,
            "captured_at",
            _canonical_instant(self.captured_at, name="captured_at"),
        )
        object.__setattr__(
            self,
            "artifact_sha256",
            _require_sha256(self.artifact_sha256, name="artifact_sha256"),
        )
        object.__setattr__(
            self,
            "response_text_sha256",
            _require_sha256(
                self.response_text_sha256,
                name="response_text_sha256",
            ),
        )
        object.__setattr__(
            self,
            "evidence_ref",
            _require_text(self.evidence_ref, name="evidence_ref"),
        )
        object.__setattr__(
            self,
            "payload_json",
            _require_text(self.payload_json, name="payload_json"),
        )


def _entry_semantics(entry: MarketProjectionEntry) -> dict[str, object]:
    return {
        "surface": entry.surface,
        "request_key": entry.request_key,
        "observation_id": entry.observation_id,
        "captured_at": entry.captured_at,
        "artifact_sha256": entry.artifact_sha256,
        "response_text_sha256": entry.response_text_sha256,
        "evidence_ref": entry.evidence_ref,
        "payload_json": entry.payload_json,
    }


def _projection_semantics(
    observation_count: int,
    entries: tuple[MarketProjectionEntry, ...],
) -> dict[str, object]:
    return {
        "schema": PROJECTION_SCHEMA,
        "observation_count": observation_count,
        "entries": [_entry_semantics(entry) for entry in entries],
    }


@dataclass(frozen=True, slots=True)
class MarketProjection:
    """Latest-observation projection over one multiset of stored records."""

    schema: str = field(default=PROJECTION_SCHEMA, kw_only=True)
    entries: tuple[MarketProjectionEntry, ...]
    observation_count: int
    projection_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema != PROJECTION_SCHEMA:
            raise ValueError(f"schema must be {PROJECTION_SCHEMA}")
        entries = tuple(self.entries)
        if not all(
            isinstance(entry, MarketProjectionEntry) for entry in entries
        ):
            raise TypeError(
                "entries must contain only MarketProjectionEntry values"
            )
        keys = [(entry.surface, entry.request_key) for entry in entries]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "entries must not repeat a (surface, request_key) key"
            )
        if keys != sorted(keys):
            raise ValueError("entries must be sorted by (surface, request_key)")
        _non_negative_int(self.observation_count, name="observation_count")
        _require_sha256(
            self.projection_fingerprint,
            name="projection_fingerprint",
        )
        expected = canonical_sha256(
            _projection_semantics(self.observation_count, entries)
        )
        if expected != self.projection_fingerprint:
            raise ValueError(
                "projection_fingerprint does not match projection semantics"
            )
        object.__setattr__(self, "entries", entries)


def _latest_key(record: MarketObservationRecord) -> tuple[str, int, str]:
    return (record.captured_at, record.source_sequence, record.observation_id)


def build_market_projection(
    records: Iterable[MarketObservationRecord],
) -> MarketProjection:
    """Select the latest record per key; deterministic for any input order."""

    latest: dict[tuple[str, str], MarketObservationRecord] = {}
    observation_count = 0
    for record in records:
        if not isinstance(record, MarketObservationRecord):
            raise TypeError(
                "records must contain only MarketObservationRecord values"
            )
        observation_count += 1
        key = (record.surface, record.request_key)
        current = latest.get(key)
        if current is None or _latest_key(record) > _latest_key(current):
            latest[key] = record

    entries = tuple(
        MarketProjectionEntry(
            surface=record.surface,
            request_key=record.request_key,
            observation_id=record.observation_id,
            captured_at=record.captured_at,
            artifact_sha256=record.artifact_sha256,
            response_text_sha256=record.response_text_sha256,
            evidence_ref=record.evidence_ref,
            payload_json=record.payload,
        )
        for record in sorted(
            latest.values(),
            key=lambda item: (item.surface, item.request_key),
        )
    )
    return MarketProjection(
        entries=entries,
        observation_count=observation_count,
        projection_fingerprint=canonical_sha256(
            _projection_semantics(observation_count, entries)
        ),
    )


def decode_payload(entry: MarketProjectionEntry) -> object:
    """Decode one projection entry payload; fail closed on invalid JSON."""

    if not isinstance(entry, MarketProjectionEntry):
        raise TypeError("entry must be MarketProjectionEntry")
    try:
        return json.loads(entry.payload_json)
    except json.JSONDecodeError as exc:
        raise MarketContractError(
            "market projection payload is not valid JSON"
        ) from exc


__all__ = [
    "PROJECTION_SCHEMA",
    "MarketProjection",
    "MarketProjectionEntry",
    "build_market_projection",
    "decode_payload",
]
