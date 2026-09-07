from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import re
from typing import Any
from urllib.parse import urlparse

from tools.bizman_collector.events import CollectorClock, EventSequencer
from tools.bizman_foundation.fingerprint import canonical_sha256
from tools.bizman_foundation.redaction import RedactionPolicy
from tools.bizman_foundation.session import new_uuid7

_MAX_BINDING_BYTES = 16 * 1024
_MAX_STRING = 256
_MAX_FIELD_NAMES = 128
_SELECTOR_RE = re.compile(
    r"^[a-z][a-z0-9-]{0,31}(?:#[A-Za-z_][A-Za-z0-9_.:-]{0,63})?$"
)


def _bounded_string(value: Any, *, limit: int = _MAX_STRING) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def _page_path(value: Any) -> str | None:
    """Normalize an origin-relative page location to pathname only."""

    text = _bounded_string(value, limit=2048)
    if text is None:
        return None
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path
    if not path.startswith("/"):
        return None
    return path[:1024] or "/"


def _form_action_path(value: Any) -> str | None:
    """Accept only the same-origin pathname shape emitted by the observer."""

    text = _bounded_string(value, limit=2048)
    if text is None:
        return None
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        return None
    path = parsed.path
    if not path.startswith("/"):
        return None
    return path[:1024] or "/"


def _wall_time_ms_iso(value: Any, fallback: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return fallback
    try:
        return datetime.fromtimestamp(numeric / 1000.0, UTC).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return fallback


def _source_monotonic_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return numeric / 1000.0


class ExecutionContextRegistry:
    """Map Runtime execution contexts to frames without persisting context payloads."""

    def __init__(self) -> None:
        self._contexts: dict[tuple[str, int], tuple[str | None, str | None]] = {}

    def register(
        self,
        *,
        session_id: str,
        context_id: int,
        frame_id: str | None,
        world_name: str | None,
    ) -> None:
        self._contexts[(session_id, int(context_id))] = (frame_id, world_name)

    def frame_for(self, session_id: str, context_id: int) -> str | None:
        item = self._contexts.get((session_id, int(context_id)))
        return item[0] if item is not None else None

    def world_for(self, session_id: str, context_id: int) -> str | None:
        item = self._contexts.get((session_id, int(context_id)))
        return item[1] if item is not None else None

    def remove_context(self, session_id: str, context_id: int) -> None:
        self._contexts.pop((session_id, int(context_id)), None)

    def clear_session(self, session_id: str) -> None:
        for key in [key for key in self._contexts if key[0] == session_id]:
            self._contexts.pop(key, None)


class ActionNormalizer:
    def __init__(
        self,
        *,
        session_id: str,
        redaction: RedactionPolicy,
        sequencer: EventSequencer,
        clock: CollectorClock,
    ) -> None:
        self.session_id = session_id
        self.redaction = redaction
        self.sequencer = sequencer
        self.clock = clock

    def _safe_selector(self, value: Any, element_name: str | None) -> str | None:
        selector = _bounded_string(value, limit=128)
        if selector is None or not _SELECTOR_RE.fullmatch(selector):
            return None
        selector_id = selector.partition("#")[2]
        if element_name is None and selector_id and self.redaction.should_drop_field(selector_id):
            return None
        if element_name is not None and self.redaction.should_drop_field(element_name):
            return None
        return selector

    def _field_names(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        output: list[str] = []
        seen: set[str] = set()
        for item in value[:_MAX_FIELD_NAMES]:
            name = _bounded_string(item, limit=128)
            if name is None or self.redaction.should_drop_field(name) or name in seen:
                continue
            seen.add(name)
            output.append(name)
        return output

    def normalize_binding(
        self,
        payload: str,
        *,
        target_id: str | None,
        frame_id: str | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, str):
            return None
        if len(payload.encode("utf-8")) > _MAX_BINDING_BYTES:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("schema") != 1:
            return None
        kind = data.get("kind")
        if kind not in {"click", "change", "submit"}:
            return None

        element = data.get("element") if isinstance(data.get("element"), dict) else {}
        form = data.get("form") if isinstance(data.get("form"), dict) else {}

        element_tag = _bounded_string(element.get("tag"), limit=32)
        if element_tag is not None:
            element_tag = element_tag.casefold()
        element_type = _bounded_string(element.get("type"), limit=32)
        if element_type is not None:
            element_type = element_type.casefold()
        element_name = _bounded_string(element.get("name"), limit=128)
        if element_name is not None and self.redaction.should_drop_field(element_name):
            element_name = None
        element_role = _bounded_string(element.get("role"), limit=64)
        safe_selector = self._safe_selector(element.get("selector"), element_name)

        form_method = _bounded_string(form.get("method"), limit=16)
        if form_method is not None:
            form_method = form_method.upper()
            if not form_method.isalpha():
                form_method = None

        event: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": new_uuid7(),
            "session_id": self.session_id,
            "sequence": self.sequencer.next(),
            "observed_at": _wall_time_ms_iso(data.get("wallTimeMs"), self.clock.wall_iso()),
            "monotonic_time": float(self.clock.monotonic()),
            "source_monotonic_time": _source_monotonic_seconds(
                data.get("performanceTimeMs")
            ),
            "source": "dom.action",
            "event_type": "dom.action",
            "confidence": "observed",
            "target_id": target_id,
            "frame_id": frame_id,
            "action_refs": [],
            "action_kind": kind,
            "is_trusted": bool(data.get("isTrusted", False)),
            "page_path": _page_path(data.get("pagePath")),
            "element_tag": element_tag,
            "element_type": element_type,
            "element_name": element_name,
            "element_role": element_role,
            "safe_selector": safe_selector,
            "form_action_path": _form_action_path(form.get("actionPath")),
            "form_method": form_method,
            "form_field_names": self._field_names(form.get("fieldNames")),
        }
        semantic = {
            key: event.get(key)
            for key in (
                "source",
                "event_type",
                "target_id",
                "frame_id",
                "action_kind",
                "is_trusted",
                "page_path",
                "element_tag",
                "element_type",
                "element_name",
                "element_role",
                "safe_selector",
                "form_action_path",
                "form_method",
                "form_field_names",
            )
        }
        event["fingerprint"] = canonical_sha256(semantic)
        return event
