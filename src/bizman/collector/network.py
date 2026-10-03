from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from bizman.collector.events import CollectorClock, EventSequencer
from bizman.collector.storage import ArtifactStore
from bizman.collector.unit_economics import (
    UnitEconomicsExtractionError,
    extract_unit_economics_payload,
)
from bizman.foundation.fingerprint import canonical_json_bytes, canonical_sha256
from bizman.foundation.redaction import RedactionPolicy, redact_headers, redact_mapping
from bizman.foundation.session import new_uuid7
from bizman.foundation.unit_economics import RESPONSE_BODY_KIND

_MAX_FIELDS = 4096
_MAX_RESPONSE_BODY_BYTES = 4 * 1024 * 1024
_COMPANY_ROSTER_PATH = "/company/"
_GOODS_PATH = "/units/shop/"
_PAGE_ARTIFACT_SCHEMA_VERSION = "1.0"
_PAGE_SANITIZER_VERSION = 2
_EXCLUDED_PAGE_TAGS = frozenset({
    "script", "style", "template", "noscript", "textarea", "svg", "iframe", "select",
})
_VOID_HTML_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_INLINE_HIDDEN_CSS_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\b",
    re.IGNORECASE,
)


class ResponseBodyCaptureError(ValueError):
    """An allowlisted response body could not be captured safely."""


class _VisibleHtmlTextParser(HTMLParser):
    """Conservative structural text extraction, never a computed CSS visibility proof."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._open_tags: list[tuple[str, bool]] = []
        self._suppressed_depth = 0
        self._title_active = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    @staticmethod
    def _is_hidden(
        tag: str, attrs: list[tuple[str, str | None]],
    ) -> bool:
        if tag in _EXCLUDED_PAGE_TAGS:
            return True
        for raw_name, raw_value in attrs:
            name = raw_name.casefold()
            value = raw_value or ""
            if name in {"hidden", "inert"}:
                return True
            if name == "aria-hidden" and value.strip().casefold() == "true":
                return True
            if name == "style" and _INLINE_HIDDEN_CSS_RE.search(value):
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        suppressed = self._is_hidden(normalized, attrs)
        if normalized not in _VOID_HTML_TAGS:
            self._open_tags.append((normalized, suppressed))
            if suppressed:
                self._suppressed_depth += 1
        self._title_active = (
            normalized == "title" and self._suppressed_depth == 0
        ) or (
            self._title_active and self._suppressed_depth == 0
        )

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index][0] == normalized:
                closed = self._open_tags[index:]
                del self._open_tags[index:]
                self._suppressed_depth -= sum(hidden for _, hidden in closed)
                self._title_active = (
                    self._suppressed_depth == 0
                    and any(name == "title" for name, _ in self._open_tags)
                )
                return

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._title_active:
            self._title_parts.append(value)
            return
        self._text_parts.append(value)

    @property
    def title(self) -> str | None:
        value = " ".join(self._title_parts).strip()
        return value or None

    @property
    def text(self) -> str:
        return "\n".join(self._text_parts)


def _decode_response_body(
    body: object,
    *,
    base64_encoded: object,
    max_bytes: int,
) -> bytes:
    if not isinstance(body, str):
        raise ResponseBodyCaptureError("CDP response body must be a string")
    if not isinstance(base64_encoded, bool):
        raise ResponseBodyCaptureError("CDP base64Encoded flag must be boolean")
    # Reject oversized CDP strings before allocating decoded response bytes.
    if base64_encoded:
        if len(body) > ((max_bytes + 2) // 3) * 4 + 8:
            raise ResponseBodyCaptureError("CDP encoded body exceeds capture size limit")
    elif len(body) > max_bytes:
        raise ResponseBodyCaptureError("CDP response body exceeds capture size limit")
    try:
        raw = (
            base64.b64decode(body, validate=True)
            if base64_encoded
            else body.encode("utf-8")
        )
    except ValueError as exc:
        raise ResponseBodyCaptureError("CDP response body encoding is invalid") from exc
    if len(raw) > max_bytes:
        raise ResponseBodyCaptureError("CDP response body exceeds capture size limit")
    return raw


def _sanitize_html_page(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResponseBodyCaptureError("company roster response is not UTF-8") from exc
    parser = _VisibleHtmlTextParser()
    parser.feed(text)
    parser.close()
    return canonical_json_bytes(
        {
            "schema_version": _PAGE_ARTIFACT_SCHEMA_VERSION,
            "sanitizer_version": _PAGE_SANITIZER_VERSION,
            "media_type": "text/html",
            "title": parser.title,
            "text": parser.text,
        }
    )


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _wall_time_iso(value: Any) -> str:
    if isinstance(value, bool):
        return _utc_now_iso()
    if isinstance(value, (int, float)) and value >= 0:
        try:
            return datetime.fromtimestamp(float(value), UTC).isoformat().replace(
                "+00:00", "Z"
            )
        except (OverflowError, OSError, ValueError):
            pass
    return _utc_now_iso()


def _status_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        code = int(value)
        if 100 <= code <= 599:
            return code
    return None


def _content_type(headers: dict[str, Any]) -> str:
    for name, value in headers.items():
        if name.casefold() == "content-type" and isinstance(value, str):
            return value.split(";", 1)[0].strip().casefold()
    return ""


def _positive_decimal(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
    ):
        return None
    return value


def _goods_route_unit_id(url: str) -> str | None:
    """Return the unit id only for the exact frozen shop-goods read surface."""

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or parsed.path != _GOODS_PATH
        or "%" in parsed.query
    ):
        return None
    try:
        values = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=_MAX_FIELDS,
        )
    except ValueError:
        return None
    if set(values) != {"id", "tab"}:
        return None
    unit_ids = values["id"]
    tabs = values["tab"]
    if len(unit_ids) != 1 or len(tabs) != 1 or tabs[0] != "goods":
        return None
    return _positive_decimal(unit_ids[0])


def _redact_json_value(value: Any, policy: RedactionPolicy) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value, policy)
    if isinstance(value, list):
        return [_redact_json_value(item, policy) for item in value]
    return value


def _structurally_redactable_json(value: Any, policy: RedactionPolicy) -> Any | None:
    """Return sanitized JSON only when top-level field semantics are available."""

    if isinstance(value, dict):
        return redact_mapping(value, policy)
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return [redact_mapping(item, policy) for item in value]
    return None


@dataclass(frozen=True, slots=True)
class FirstPartyPolicy:
    hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = tuple(
            dict.fromkeys(
                host.casefold().strip().rstrip(".")
                for host in self.hosts
                if host.strip()
            )
        )
        if not normalized:
            raise ValueError("at least one first-party host is required")
        object.__setattr__(self, "hosts", normalized)

    def matches_host(self, host: str | None) -> bool:
        if not host:
            return False
        candidate = host.casefold().rstrip(".")
        return any(
            candidate == root or candidate.endswith(f".{root}") for root in self.hosts
        )

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https", "ws", "wss"} and self.matches_host(
            parsed.hostname
        )


class NetworkNormalizer:
    def __init__(
        self,
        *,
        session_id: str,
        first_party: FirstPartyPolicy,
        redaction: RedactionPolicy,
        artifacts: ArtifactStore,
        sequencer: EventSequencer | None = None,
        clock: CollectorClock | None = None,
        max_response_body_bytes: int = _MAX_RESPONSE_BODY_BYTES,
    ) -> None:
        self.session_id = session_id
        self.first_party = first_party
        self.redaction = redaction
        self.artifacts = artifacts
        self.sequencer = sequencer or EventSequencer()
        self.clock = clock or CollectorClock()
        if max_response_body_bytes <= 0:
            raise ValueError("max_response_body_bytes must be positive")
        self.max_response_body_bytes = int(max_response_body_bytes)
        self._requests: dict[tuple[str | None, str], dict[str, Any]] = {}
        self._websockets: dict[str, dict[str, Any]] = {}

    def _query(self, url: str) -> dict[str, list[str]]:
        parsed = urlparse(url)
        try:
            values = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=_MAX_FIELDS,
            )
        except ValueError:
            return {}
        return {
            key: [str(item) for item in items]
            for key, items in values.items()
            if not self.redaction.should_drop_field(key)
        }

    @staticmethod
    def _path(url: str) -> str:
        parsed = urlparse(url)
        return parsed.path or "/"

    def _body_ref(self, request: dict[str, Any]) -> str | None:
        post_data = request.get("postData")
        headers = request.get("headers")
        if not isinstance(post_data, str) or not isinstance(headers, dict):
            return None
        encoded = post_data.encode("utf-8")
        if len(encoded) > self.redaction.max_request_bytes:
            return None

        mime = _content_type(headers)
        if mime not in {value.casefold() for value in self.redaction.mime_allowlist}:
            return None

        sanitized: bytes | None = None
        if mime == "application/json":
            try:
                parsed = json.loads(post_data)
            except json.JSONDecodeError:
                return None
            structured = _structurally_redactable_json(parsed, self.redaction)
            if structured is None:
                return None
            sanitized = canonical_json_bytes(structured)
        elif mime == "application/x-www-form-urlencoded":
            try:
                parsed_form = parse_qs(
                    post_data,
                    keep_blank_values=True,
                    strict_parsing=False,
                    max_num_fields=_MAX_FIELDS,
                )
            except ValueError:
                return None
            filtered = {
                key: [str(item) for item in values]
                for key, values in parsed_form.items()
                if not self.redaction.should_drop_field(key)
            }
            sanitized = urlencode(filtered, doseq=True).encode("utf-8")
        if sanitized is None:
            return None
        return self.artifacts.put_bytes(sanitized)

    def _base_event(
        self,
        *,
        event_type: str,
        params: dict[str, Any],
        target_id: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        source_monotonic = params.get("timestamp")
        if (
            isinstance(source_monotonic, bool)
            or not isinstance(source_monotonic, (int, float))
            or source_monotonic < 0
        ):
            source_monotonic = None
        wall_time = params.get("wallTime")
        observed_at = (
            _wall_time_iso(wall_time)
            if not isinstance(wall_time, bool) and isinstance(wall_time, (int, float))
            else self.clock.wall_iso()
        )
        return {
            "schema_version": "1.0",
            "event_id": new_uuid7(),
            "session_id": self.session_id,
            "sequence": self.sequencer.next(),
            "observed_at": observed_at,
            "monotonic_time": float(self.clock.monotonic()),
            "source_monotonic_time": (
                float(source_monotonic) if source_monotonic is not None else None
            ),
            "source": "cdp.network",
            "event_type": event_type,
            "confidence": "observed",
            "target_id": target_id,
            "frame_id": params.get("frameId") if isinstance(params.get("frameId"), str) else None,
            "loader_id": params.get("loaderId") if isinstance(params.get("loaderId"), str) else None,
            "request_id": request_id,
            "action_refs": [],
        }

    def _finish_event(self, event: dict[str, Any]) -> dict[str, Any]:
        semantic = {
            key: event.get(key)
            for key in (
                "source",
                "event_type",
                "target_id",
                "frame_id",
                "loader_id",
                "request_id",
                "redirect_index",
                "method",
                "url_path",
                "status_code",
                "query",
                "initiator_type",
                "has_user_gesture",
                "request_body_ref",
                "response_body_ref",
                "websocket_opcode",
                "error_text",
            )
        }
        if "response_body_kind" in event:
            # Conditional so legacy event fingerprints stay byte-identical.
            semantic["response_body_kind"] = event.get("response_body_kind")
        event["fingerprint"] = canonical_sha256(semantic)
        return event

    def normalize(
        self,
        *,
        method: str,
        params: dict[str, Any],
        target_id: str | None,
    ) -> dict[str, Any] | None:
        handler = getattr(self, f"_on_{method.replace('.', '_')}", None)
        if handler is None:
            return None
        return handler(params, target_id)

    def _on_Network_requestWillBeSent(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        request = params.get("request")
        if not isinstance(request_id, str) or not isinstance(request, dict):
            return None
        url = request.get("url")
        if not isinstance(url, str):
            return None

        previous = self._requests.get((target_id, request_id))
        redirect_response = params.get("redirectResponse")
        redirect_index = int(previous.get("redirect_index", 0)) if previous else 0
        redirect_from_path = None
        redirect_status_code = None
        if isinstance(redirect_response, dict):
            redirect_index = redirect_index + 1 if previous else 1
            redirect_url = redirect_response.get("url")
            if isinstance(redirect_url, str) and self.first_party.matches_url(redirect_url):
                redirect_from_path = self._path(redirect_url)
            redirect_status_code = _status_code(redirect_response.get("status"))

        if not self.first_party.matches_url(url):
            self._requests.pop((target_id, request_id), None)
            return None

        request_method = request.get("method")
        headers = request.get("headers")
        safe_headers = redact_headers(headers, self.redaction) if isinstance(headers, dict) else {}
        body_ref = self._body_ref(request)
        state = {
            "url": url,
            "method": request_method if isinstance(request_method, str) else None,
            "redirect_index": redirect_index,
            "query": self._query(url),
            "status_code": None,
            "mime_type": None,
        }
        self._requests[(target_id, request_id)] = state

        event = self._base_event(
            event_type="http.request",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "redirect_index": redirect_index,
                "method": state["method"],
                "url_path": self._path(url),
                "route_pattern": None,
                "status_code": None,
                "query": self._query(url),
                "headers": safe_headers,
                "request_body_ref": body_ref,
                "response_body_ref": None,
                "initiator_type": (
                    params.get("initiator", {}).get("type")
                    if isinstance(params.get("initiator"), dict)
                    and isinstance(params.get("initiator", {}).get("type"), str)
                    else None
                ),
                "has_user_gesture": params.get("hasUserGesture") is True,
            }
        )
        if redirect_from_path is not None:
            event["redirect_from_path"] = redirect_from_path
        if redirect_status_code is not None:
            event["redirect_status_code"] = redirect_status_code
        return self._finish_event(event)

    def _on_Network_responseReceived(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        response = params.get("response")
        if not isinstance(request_id, str) or not isinstance(response, dict):
            return None
        state = self._requests.get((target_id, request_id))
        if state is None:
            return None
        url = response.get("url") if isinstance(response.get("url"), str) else state["url"]
        if not self.first_party.matches_url(url):
            self._requests.pop((target_id, request_id), None)
            return None
        headers = response.get("headers")
        status_code = _status_code(response.get("status"))
        mime_type = (
            response.get("mimeType")
            if isinstance(response.get("mimeType"), str)
            else None
        )
        state["status_code"] = status_code
        state["mime_type"] = mime_type
        event = self._base_event(
            event_type="http.response",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "redirect_index": state["redirect_index"],
                "method": state["method"],
                "url_path": self._path(url),
                "route_pattern": None,
                "status_code": status_code,
                "query": self._query(url),
                "headers": redact_headers(headers, self.redaction) if isinstance(headers, dict) else {},
                "request_body_ref": None,
                "response_body_ref": None,
                "mime_type": mime_type,
                "protocol": response.get("protocol") if isinstance(response.get("protocol"), str) else None,
            }
        )
        return self._finish_event(event)

    def _capture_plan(
        self, request_id: object, *, target_id: str | None,
    ) -> tuple[str, str | None] | None:
        if not isinstance(request_id, str):
            return None
        state = self._requests.get((target_id, request_id))
        if state is None:
            return None
        if state.get("method") != "GET":
            return None
        if state.get("status_code") != 200:
            return None
        mime_type = str(state.get("mime_type", ""))
        mime_base, _separator, _parameters = mime_type.partition(";")
        if mime_base.casefold() != "text/html":
            return None
        url = state.get("url")
        if not isinstance(url, str):
            return None
        path = self._path(url)
        if path == _COMPANY_ROSTER_PATH:
            query = state.get("query")
            if not isinstance(query, dict):
                return None
            company_ids = query.get("id")
            tabs = query.get("tab")
            if not isinstance(company_ids, list) or len(company_ids) != 1:
                return None
            company_id = next(iter(company_ids), None)
            if _positive_decimal(company_id) is None:
                return None
            if tabs != ["units"]:
                return None
            return ("company-roster", None)
        if path == _GOODS_PATH:
            unit_id = _goods_route_unit_id(url)
            if unit_id is None:
                return None
            return ("unit-economics", unit_id)
        return None

    def response_body_capture_candidate(
        self, request_id: object, *, target_id: str | None = None,
    ) -> bool:
        return self._capture_plan(request_id, target_id=target_id) is not None

    def normalize_response_body(
        self,
        *,
        request_id: str,
        body: object,
        base64_encoded: object,
        params: dict[str, Any],
        target_id: str | None,
    ) -> dict[str, Any] | None:
        plan = self._capture_plan(request_id, target_id=target_id)
        if plan is None:
            return None
        surface, unit_id = plan
        state = self._requests.get((target_id, request_id))
        if state is None:
            return None
        raw = _decode_response_body(
            body,
            base64_encoded=base64_encoded,
            max_bytes=self.max_response_body_bytes,
        )
        if surface == "unit-economics":
            if unit_id is None:
                raise ResponseBodyCaptureError(
                    "unit economics capture plan is missing the unit id"
                )
            try:
                artifact_ref = self.artifacts.put_bytes(
                    extract_unit_economics_payload(raw, unit_id=unit_id)
                )
            except UnitEconomicsExtractionError as exc:
                # No artifact and no event: the page is not proven typed evidence.
                raise ResponseBodyCaptureError(
                    "unit economics response body extraction failed"
                ) from exc
        else:
            artifact_ref = self.artifacts.put_bytes(_sanitize_html_page(raw))
        event = self._base_event(
            event_type="http.response_body",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        url = state.get("url")
        if not isinstance(url, str):
            raise ResponseBodyCaptureError(
                "captured response body request state is missing URL"
            )
        event.update(
            {
                "redirect_index": state.get("redirect_index", 0),
                "method": state.get("method"),
                "url_path": self._path(url),
                "route_pattern": None,
                "status_code": state.get("status_code"),
                "query": state.get("query"),
                "mime_type": state.get("mime_type"),
                "request_body_ref": None,
                "response_body_ref": artifact_ref,
            }
        )
        if surface == "unit-economics":
            event["response_body_kind"] = RESPONSE_BODY_KIND
        return self._finish_event(event)

    def _on_Network_loadingFinished(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return None
        state = self._requests.pop((target_id, request_id), None)
        if state is None:
            return None
        event = self._base_event(
            event_type="http.finished",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        encoded = params.get("encodedDataLength")
        encoded_length = None
        if (
            not isinstance(encoded, bool)
            and isinstance(encoded, (int, float))
            and encoded >= 0
        ):
            encoded_length = float(encoded)
        event.update(
            {
                "redirect_index": state["redirect_index"],
                "method": state["method"],
                "url_path": self._path(state["url"]),
                "route_pattern": None,
                "status_code": None,
                "encoded_data_length": encoded_length,
            }
        )
        return self._finish_event(event)

    def _on_Network_loadingFailed(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return None
        state = self._requests.pop((target_id, request_id), None)
        if state is None:
            return None
        event = self._base_event(
            event_type="http.failed",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "redirect_index": state["redirect_index"],
                "method": state["method"],
                "url_path": self._path(state["url"]),
                "route_pattern": None,
                "status_code": None,
                "error_text": params.get("errorText") if isinstance(params.get("errorText"), str) else None,
                "canceled": params.get("canceled") is True,
            }
        )
        return self._finish_event(event)

    def _on_Network_webSocketCreated(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        url = params.get("url")
        if not isinstance(request_id, str) or not isinstance(url, str):
            return None
        if not self.first_party.matches_url(url):
            self._websockets.pop(request_id, None)
            return None
        self._websockets[request_id] = {"url": url}
        event = self._base_event(
            event_type="websocket.created",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "url_path": self._path(url),
                "query": self._query(url),
                "method": None,
                "status_code": None,
            }
        )
        return self._finish_event(event)

    def _websocket_frame(
        self,
        params: dict[str, Any],
        target_id: str | None,
        *,
        direction: str,
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or request_id not in self._websockets:
            return None
        frame = params.get("response")
        if not isinstance(frame, dict):
            return None
        opcode = frame.get("opcode")
        event = self._base_event(
            event_type=f"websocket.frame.{direction}",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "url_path": self._path(self._websockets[request_id]["url"]),
                "method": None,
                "status_code": None,
                "websocket_opcode": (
                    opcode
                    if isinstance(opcode, int) and not isinstance(opcode, bool)
                    else None
                ),
                "websocket_masked": frame.get("mask") if isinstance(frame.get("mask"), bool) else None,
            }
        )
        return self._finish_event(event)

    def _on_Network_webSocketFrameReceived(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        return self._websocket_frame(params, target_id, direction="received")

    def _on_Network_webSocketFrameSent(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        return self._websocket_frame(params, target_id, direction="sent")

    def _on_Network_webSocketClosed(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return None
        state = self._websockets.pop(request_id, None)
        if state is None:
            return None
        event = self._base_event(
            event_type="websocket.closed",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        event.update(
            {
                "url_path": self._path(state["url"]),
                "method": None,
                "status_code": None,
            }
        )
        return self._finish_event(event)

