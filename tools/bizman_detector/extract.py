from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import parse_qs

from tools.bizman_detector.evidence import (
    EvidenceIdentity,
    EvidenceIntegrityError,
    EvidenceReader,
)
from tools.bizman_detector.model import (
    FormObservation,
    HttpObservation,
    ObservationSet,
    RelationObservation,
    RuntimeContract,
)
from tools.bizman_detector.normalization import (
    AmbiguousPathError,
    PathMatcher,
    normalize_key_set,
    normalize_method,
    normalize_origin_relative_path,
)
from tools.bizman_foundation.redaction import RedactionPolicy


_MAX_FORM_FIELDS = 4096
_DEFAULT_MAX_PENDING_RELATION_SOURCES = 65_536
_SUPPORTED_BODY_MIME = frozenset(
    {"application/json", "application/x-www-form-urlencoded"}
)


class ExtractionError(ValueError):
    """Base class for detector observation extraction failures."""


class ExtractionIntegrityError(ExtractionError):
    """Validated evidence is structurally inconsistent with collector semantics."""


@dataclass(slots=True)
class _HttpBuilder:
    method: str
    literal_path: str
    canonical_path_pattern: str | None
    query_keys: tuple[str, ...]
    body_keys: tuple[str, ...] | None
    request_event_id: str
    response_event_id: str | None = None
    status: int | None = None

    def freeze(self) -> HttpObservation:
        return HttpObservation(
            method=self.method,
            literal_path=self.literal_path,
            canonical_path_pattern=self.canonical_path_pattern,
            query_keys=self.query_keys,
            status=self.status,
            body_keys=self.body_keys,
            request_event_id=self.request_event_id,
            response_event_id=self.response_event_id,
        )


@dataclass(frozen=True, slots=True)
class _ActionSource:
    method: str | None
    action_path: str | None


@dataclass(frozen=True, slots=True)
class _RequestSource:
    method: str
    literal_path: str
    path_pattern: str | None


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _header_content_type(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for raw_name, raw_value in value.items():
        if (
            isinstance(raw_name, str)
            and raw_name.casefold() == "content-type"
            and isinstance(raw_value, str)
        ):
            mime = raw_value.split(";", 1)[0].strip().casefold()
            return mime or None
    return None


def _decode_utf8(payload: bytes, *, ref: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionIntegrityError(
            f"referenced request body {ref} is not valid UTF-8"
        ) from exc


def _status_code(value: object, *, source: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ExtractionIntegrityError(f"{source}: invalid {label} status")
    return value


class ObservationExtractor:
    """Project verified runtime events into compact immutable value-free IR."""

    def __init__(
        self,
        contract: RuntimeContract,
        evidence_reader: EvidenceReader,
        redaction: RedactionPolicy,
        *,
        max_pending_relation_sources: int = _DEFAULT_MAX_PENDING_RELATION_SOURCES,
    ) -> None:
        self.contract = contract
        self.evidence_reader = evidence_reader
        self.redaction = redaction
        self.max_pending_relation_sources = _positive_int(
            max_pending_relation_sources,
            name="max_pending_relation_sources",
        )
        self._matcher = PathMatcher(endpoint.path_pattern for endpoint in contract.endpoints)

    def _canonical_path(self, raw_path: object, *, source: str) -> tuple[str, str | None]:
        try:
            literal = normalize_origin_relative_path(raw_path)
            match = self._matcher.match(literal)
        except (ValueError, AmbiguousPathError) as exc:
            raise ExtractionIntegrityError(
                f"{source}: path {raw_path!r} cannot be normalized/matched deterministically"
            ) from exc
        return literal, match.path_pattern if match.matched else None

    def _form_action_path(self, raw_path: object, *, source: str) -> str:
        literal, pattern = self._canonical_path(raw_path, source=source)
        return pattern or literal

    def _body_keys(self, event: Mapping[str, Any], *, source: str) -> tuple[str, ...] | None:
        ref = event.get("request_body_ref")
        if ref is None:
            return None
        if not isinstance(ref, str):
            raise ExtractionIntegrityError(f"{source}: request_body_ref must be a string or null")

        mime = _header_content_type(event.get("headers"))
        if mime not in _SUPPORTED_BODY_MIME:
            raise ExtractionIntegrityError(
                f"{source}: persisted request body {ref} has unsupported/unknown content type {mime!r}"
            )
        payload = self.evidence_reader.read_verified_artifact(ref)
        text = _decode_utf8(payload, ref=ref)

        if mime == "application/json":
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ExtractionIntegrityError(
                    f"{source}: persisted JSON request body {ref} is malformed"
                ) from exc
            if isinstance(value, dict):
                keys: list[object] = list(value.keys())
            elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
                keys = [key for item in value for key in item.keys()]
            else:
                raise ExtractionIntegrityError(
                    f"{source}: persisted JSON request body {ref} is not an object/list-of-objects"
                )
            return normalize_key_set(keys, self.redaction)

        try:
            form = parse_qs(
                text,
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=_MAX_FORM_FIELDS,
            )
        except ValueError as exc:
            raise ExtractionIntegrityError(
                f"{source}: persisted form request body {ref} is malformed/oversized"
            ) from exc
        return normalize_key_set(form.keys(), self.redaction)

    @staticmethod
    def _request_key(event: Mapping[str, Any], *, source: str) -> tuple[str, int]:
        request_id = event.get("request_id")
        redirect_index = event.get("redirect_index")
        if not isinstance(request_id, str) or not request_id:
            raise ExtractionIntegrityError(f"{source}: HTTP event lacks request_id")
        if isinstance(redirect_index, bool) or not isinstance(redirect_index, int) or redirect_index < 0:
            raise ExtractionIntegrityError(f"{source}: HTTP event has invalid redirect_index")
        return request_id, redirect_index

    def _attach_redirect_status(
        self,
        event: Mapping[str, Any],
        *,
        event_id: str,
        source: str,
        key: tuple[str, int],
        requests_by_transport: dict[tuple[str, int], _HttpBuilder],
    ) -> None:
        redirect_from = event.get("redirect_from_path")
        redirect_status = event.get("redirect_status_code")
        if redirect_from is None and redirect_status is None:
            return
        if redirect_from is None or redirect_status is None:
            raise ExtractionIntegrityError(
                f"{source}: redirect metadata must include both path and status"
            )
        if not isinstance(redirect_from, str):
            raise ExtractionIntegrityError(f"{source}: redirect_from_path must be a string")
        status = _status_code(redirect_status, source=source, label="redirect")
        if key[1] <= 0:
            raise ExtractionIntegrityError(
                f"{source}: redirect metadata requires positive redirect_index"
            )
        previous_key = (key[0], key[1] - 1)
        previous = requests_by_transport.get(previous_key)
        if previous is None:
            raise ExtractionIntegrityError(
                f"{source}: first-party redirect has no preceding request {previous_key!r}"
            )
        previous_path, _ = self._canonical_path(redirect_from, source=source)
        if previous_path != previous.literal_path:
            raise ExtractionIntegrityError(
                f"{source}: redirect_from_path disagrees with preceding request"
            )
        if previous.status is not None and previous.status != status:
            raise ExtractionIntegrityError(
                f"{source}: redirect status conflicts with preceding response status"
            )
        previous.status = status
        if previous.response_event_id is None:
            # requestWillBeSent carries redirectResponse for the previous hop, so
            # this event is the immutable provenance for that observed status.
            previous.response_event_id = event_id

    def _add_relation_source(
        self,
        action_sources: dict[str, _ActionSource],
        request_sources: dict[str, _RequestSource],
    ) -> None:
        if len(action_sources) + len(request_sources) > self.max_pending_relation_sources:
            raise ExtractionIntegrityError(
                "pending action/request source state exceeded configured hard limit"
            )

    def extract(self, identity: EvidenceIdentity) -> ObservationSet:
        if not isinstance(identity, EvidenceIdentity):
            raise TypeError("identity must be EvidenceIdentity")

        builders: list[_HttpBuilder] = []
        requests_by_transport: dict[tuple[str, int], _HttpBuilder] = {}
        forms: list[FormObservation] = []
        relations: list[RelationObservation] = []
        action_sources: dict[str, _ActionSource] = {}
        request_sources: dict[str, _RequestSource] = {}

        for event in self.evidence_reader.iter_events(
            identity.session_id,
            expected_identity=identity,
        ):
            event_id = event.get("event_id")
            assert isinstance(event_id, str)
            event_type = event.get("event_type")
            source = f"event {event_id}"

            if event_type == "http.request" and event.get("source") == "cdp.network":
                try:
                    method = normalize_method(event.get("method"))
                except ValueError as exc:
                    raise ExtractionIntegrityError(f"{source}: invalid HTTP method") from exc
                literal_path, path_pattern = self._canonical_path(
                    event.get("url_path"), source=source
                )
                query = event.get("query")
                if not isinstance(query, Mapping):
                    raise ExtractionIntegrityError(f"{source}: HTTP request query must be an object")
                query_keys = normalize_key_set(query.keys(), self.redaction)
                body_keys = self._body_keys(event, source=source)
                key = self._request_key(event, source=source)
                if key in requests_by_transport:
                    raise ExtractionIntegrityError(
                        f"{source}: duplicate HTTP request transport identity {key!r}"
                    )
                self._attach_redirect_status(
                    event,
                    event_id=event_id,
                    source=source,
                    key=key,
                    requests_by_transport=requests_by_transport,
                )
                builder = _HttpBuilder(
                    method=method,
                    literal_path=literal_path,
                    canonical_path_pattern=path_pattern,
                    query_keys=query_keys,
                    body_keys=body_keys,
                    request_event_id=event_id,
                )
                builders.append(builder)
                requests_by_transport[key] = builder
                if event_id in request_sources:
                    raise ExtractionIntegrityError(f"{source}: duplicate request source event id")
                request_sources[event_id] = _RequestSource(method, literal_path, path_pattern)
                self._add_relation_source(action_sources, request_sources)
                continue

            if event_type == "http.response" and event.get("source") == "cdp.network":
                key = self._request_key(event, source=source)
                builder = requests_by_transport.get(key)
                if builder is None:
                    raise ExtractionIntegrityError(
                        f"{source}: HTTP response has no preceding request {key!r}"
                    )
                if builder.response_event_id is not None:
                    raise ExtractionIntegrityError(
                        f"{source}: duplicate HTTP response/status for request {key!r}"
                    )
                try:
                    response_method = normalize_method(event.get("method"))
                except ValueError as exc:
                    raise ExtractionIntegrityError(f"{source}: invalid response method") from exc
                response_path, _ = self._canonical_path(event.get("url_path"), source=source)
                if response_method != builder.method or response_path != builder.literal_path:
                    raise ExtractionIntegrityError(
                        f"{source}: response method/path disagrees with preceding request"
                    )
                status = event.get("status_code")
                if status is not None:
                    builder.status = _status_code(status, source=source, label="response")
                builder.response_event_id = event_id
                continue

            if event_type in {"dom.action", "bas.action"} and event.get("source") in {
                "dom.action",
                "bas.action",
            }:
                raw_method = event.get("form_method")
                raw_action_path = event.get("form_action_path")
                action_method: str | None = None
                action_path: str | None = None
                if raw_method is not None:
                    try:
                        action_method = normalize_method(raw_method)
                    except ValueError as exc:
                        raise ExtractionIntegrityError(f"{source}: invalid form method") from exc
                if raw_action_path is not None:
                    action_path = self._form_action_path(raw_action_path, source=source)

                if action_method is not None and action_path is not None:
                    raw_fields = event.get("form_field_names", [])
                    if not isinstance(raw_fields, list):
                        raise ExtractionIntegrityError(
                            f"{source}: form_field_names must be an array"
                        )
                    forms.append(
                        FormObservation(
                            method=action_method,
                            action_path=action_path,
                            field_names=normalize_key_set(raw_fields, self.redaction),
                            action_event_id=event_id,
                        )
                    )

                if event_id in action_sources:
                    raise ExtractionIntegrityError(f"{source}: duplicate action source event id")
                action_sources[event_id] = _ActionSource(action_method, action_path)
                self._add_relation_source(action_sources, request_sources)
                continue

            if event_type == "correlation.action_http" and event.get("source") == "system":
                action_event_id = event.get("action_event_id")
                request_event_id = event.get("network_event_id")
                status = event.get("correlation_status")
                if (
                    not isinstance(action_event_id, str)
                    or not isinstance(request_event_id, str)
                    or not isinstance(status, str)
                ):
                    raise ExtractionIntegrityError(f"{source}: malformed correlation references")
                action_refs = event.get("action_refs")
                if action_refs != [action_event_id]:
                    raise ExtractionIntegrityError(
                        f"{source}: action_refs disagrees with action_event_id"
                    )
                action = action_sources.get(action_event_id)
                request = request_sources.get(request_event_id)
                if action is None or request is None:
                    raise ExtractionIntegrityError(
                        f"{source}: correlation references unresolved same-session sources"
                    )
                relations.append(
                    RelationObservation(
                        correlation_status=status,
                        action_event_id=action_event_id,
                        request_event_id=request_event_id,
                        action_method=action.method,
                        action_path=action.action_path,
                        request_method=request.method,
                        request_literal_path=request.literal_path,
                        request_path_pattern=request.path_pattern,
                    )
                )

        # The streaming pass above proves that the bytes consumed by extraction
        # match identity. Re-inspect once to catch mutation after a file was read
        # but before the ObservationSet is returned.
        if self.evidence_reader.inspect(identity.session_id) != identity:
            raise EvidenceIntegrityError(
                f"session {identity.session_id} changed while observations were extracted"
            )

        return ObservationSet(
            http=tuple(builder.freeze() for builder in builders),
            forms=tuple(forms),
            relations=tuple(relations),
        )


__all__ = [
    "ExtractionError",
    "ExtractionIntegrityError",
    "ObservationExtractor",
]
