from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_foundation.fingerprint import canonical_json_bytes, canonical_sha256
from tools.bizman_foundation.redaction import RedactionPolicy, redact_headers, redact_mapping
from tools.bizman_foundation.session import new_uuid7

_MAX_FIELDS = 4096


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _wall_time_iso(value: Any) -> str:
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


def _redact_json_value(value: Any, policy: RedactionPolicy) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value, policy)
    if isinstance(value, list):
        return [_redact_json_value(item, policy) for item in value]
    return value


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
    ) -> None:
        self.session_id = session_id
        self.first_party = first_party
        self.redaction = redaction
        self.artifacts = artifacts
        self._sequence = 0
        self._requests: dict[str, dict[str, Any]] = {}
        self._websockets: dict[str, dict[str, Any]] = {}

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

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
            sanitized = canonical_json_bytes(
                _redact_json_value(parsed, self.redaction)
            )
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
        monotonic = params.get("timestamp", 0.0)
        if not isinstance(monotonic, (int, float)) or monotonic < 0:
            monotonic = 0.0
        return {
            "schema_version": "1.0",
            "event_id": new_uuid7(),
            "session_id": self.session_id,
            "sequence": self._next_sequence(),
            "observed_at": _wall_time_iso(params.get("wallTime")),
            "monotonic_time": float(monotonic),
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
                "websocket_opcode",
                "error_text",
            )
        }
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

        previous = self._requests.get(request_id)
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
            self._requests.pop(request_id, None)
            return None

        request_method = request.get("method")
        headers = request.get("headers")
        safe_headers = redact_headers(headers, self.redaction) if isinstance(headers, dict) else {}
        body_ref = self._body_ref(request)
        state = {
            "url": url,
            "method": request_method if isinstance(request_method, str) else None,
            "redirect_index": redirect_index,
        }
        self._requests[request_id] = state

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
                "has_user_gesture": bool(params.get("hasUserGesture", False)),
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
        state = self._requests.get(request_id)
        if state is None:
            return None
        url = response.get("url") if isinstance(response.get("url"), str) else state["url"]
        if not self.first_party.matches_url(url):
            self._requests.pop(request_id, None)
            return None
        headers = response.get("headers")
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
                "status_code": _status_code(response.get("status")),
                "query": self._query(url),
                "headers": redact_headers(headers, self.redaction) if isinstance(headers, dict) else {},
                "request_body_ref": None,
                "response_body_ref": None,
                "mime_type": response.get("mimeType") if isinstance(response.get("mimeType"), str) else None,
                "protocol": response.get("protocol") if isinstance(response.get("protocol"), str) else None,
            }
        )
        return self._finish_event(event)

    def _on_Network_loadingFinished(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return None
        state = self._requests.pop(request_id, None)
        if state is None:
            return None
        event = self._base_event(
            event_type="http.finished",
            params=params,
            target_id=target_id,
            request_id=request_id,
        )
        encoded = params.get("encodedDataLength")
        event.update(
            {
                "redirect_index": state["redirect_index"],
                "method": state["method"],
                "url_path": self._path(state["url"]),
                "route_pattern": None,
                "status_code": None,
                "encoded_data_length": float(encoded) if isinstance(encoded, (int, float)) and encoded >= 0 else None,
            }
        )
        return self._finish_event(event)

    def _on_Network_loadingFailed(
        self, params: dict[str, Any], target_id: str | None
    ) -> dict[str, Any] | None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return None
        state = self._requests.pop(request_id, None)
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
                "canceled": bool(params.get("canceled", False)),
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
                "websocket_opcode": frame.get("opcode") if isinstance(frame.get("opcode"), int) else None,
                "websocket_masked": frame.get("mask") if isinstance(frame.get("mask"), bool) else None,
            }
        )
        return self._finish_event(event)

    def _on_Network_webSocketFrameReceived(self, params: dict[str, Any], target_id: str | None) -> dict[str, Any] | None:
        return self._websocket_frame(params, target_id, direction="received")

    def _on_Network_webSocketFrameSent(self, params: dict[str, Any], target_id: str | None) -> dict[str, Any] | None:
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
        event.update({"url_path": self._path(state["url"]), "method": None, "status_code": None})
        return self._finish_event(event)
