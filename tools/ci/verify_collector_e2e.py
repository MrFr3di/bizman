#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs

from jsonschema import Draft202012Validator, FormatChecker


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_path(data_dir: Path, ref: str) -> Path:
    algorithm, digest = ref.split(":", 1)
    if algorithm != "sha256" or len(digest) != 64:
        raise AssertionError(f"invalid artifact ref: {ref}")
    return data_dir / "artifacts" / "sha256" / digest[:2] / digest


def _request_for_path(events: list[dict], path: str) -> dict:
    matches = [
        event
        for event in events
        if event.get("event_type") == "http.request"
        and event.get("url_path") == path
    ]
    if not matches:
        raise AssertionError(f"missing HTTP request event for {path}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()

    data_dir = args.data_dir
    manifests = sorted((data_dir / "sessions").glob("*/manifest.json"))
    if len(manifests) != 1:
        raise AssertionError(f"expected exactly one manifest, found {len(manifests)}")

    manifest = _load_json(manifests[0])
    manifest_schema = _load_json(args.repo_root / "schemas/session-manifest.schema.json")
    errors = list(
        Draft202012Validator(
            manifest_schema, format_checker=FormatChecker()
        ).iter_errors(manifest)
    )
    if errors:
        raise AssertionError(f"invalid session manifest: {[error.message for error in errors]}")
    if manifest["status"] not in {"completed", "cancelled"}:
        raise AssertionError(f"unexpected terminal status: {manifest['status']}")

    protocol_ref = manifest["protocol"]["artifact_ref"]
    if not isinstance(protocol_ref, str) or not _artifact_path(data_dir, protocol_ref).is_file():
        raise AssertionError("protocol artifact is missing")

    event_schema = _load_json(args.repo_root / "schemas/event.schema.json")
    validator = Draft202012Validator(event_schema, format_checker=FormatChecker())
    events: list[dict] = []
    for rel in manifest["event_files"]:
        path = data_dir / rel
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            event = json.loads(line)
            problems = list(validator.iter_errors(event))
            if problems:
                raise AssertionError(
                    f"invalid event {path}:{lineno}: {[item.message for item in problems]}"
                )
            events.append(event)

    if not events:
        raise AssertionError("collector produced no events")
    sequences = [event["sequence"] for event in events]
    if sequences != list(range(len(events))):
        raise AssertionError(f"event sequence is not contiguous: {sequences[:20]}")

    request_paths = {
        event.get("url_path")
        for event in events
        if event.get("event_type") == "http.request"
    }
    for required in {"/api/get", "/api/post", "/redirect", "/api/action-post"}:
        if required not in request_paths:
            raise AssertionError(f"missing expected request path {required}; got {sorted(request_paths)}")

    post_event = _request_for_path(events, "/api/post")
    body_ref = post_event.get("request_body_ref")
    if not isinstance(body_ref, str):
        raise AssertionError("sanitized JSON POST body was not persisted")
    body = _artifact_path(data_dir, body_ref).read_text(encoding="utf-8")
    if json.loads(body) != {"safe": "kept"}:
        raise AssertionError(f"unexpected sanitized POST artifact: {body}")

    action_post = _request_for_path(events, "/api/action-post")
    action_body_ref = action_post.get("request_body_ref")
    if not isinstance(action_body_ref, str):
        raise AssertionError("sanitized action POST body was not persisted")
    action_body = _artifact_path(data_dir, action_body_ref).read_text(encoding="utf-8")
    if parse_qs(action_body, keep_blank_values=True) != {"product": ["42"]}:
        raise AssertionError(f"unexpected sanitized action POST artifact: {action_body}")

    submit_actions = [
        event
        for event in events
        if event.get("event_type") == "dom.action"
        and event.get("action_kind") == "submit"
        and event.get("form_action_path") == "/api/action-post"
    ]
    if not submit_actions:
        raise AssertionError("missing normalized DOM submit action for /api/action-post")
    action = submit_actions[0]
    if action.get("form_method") != "POST":
        raise AssertionError(f"unexpected submit method: {action.get('form_method')}")
    if action.get("form_field_names") != ["product"]:
        raise AssertionError(
            f"unexpected sanitized form field names: {action.get('form_field_names')}"
        )

    links = [
        event
        for event in events
        if event.get("event_type") == "correlation.action_http"
        and event.get("action_event_id") == action.get("event_id")
        and event.get("network_event_id") == action_post.get("event_id")
    ]
    if not links:
        raise AssertionError("missing immutable action→HTTP correlation event")
    link = links[0]
    if link.get("correlation_status") not in {"strong", "probable"}:
        raise AssertionError(f"unexpected correlation status: {link.get('correlation_status')}")
    if link.get("correlation_status") == "exact":
        raise AssertionError("heuristic action correlation must never be exact")

    serialized_events = "\n".join(json.dumps(event, sort_keys=True) for event in events)
    forbidden = (
        "TOP_SECRET_QUERY",
        "TOP_SECRET_BODY",
        "TOP_SECRET_WS_QUERY",
        "TOP_SECRET_WS_PAYLOAD",
        "TOP_SECRET_ACTION_QUERY",
        "TOP_SECRET_INPUT_VALUE",
        "clientSecret",
        "accessToken",
    )
    for value in forbidden:
        if value in serialized_events or value in body or value in action_body:
            raise AssertionError(f"secret leaked into durable output: {value}")

    # None of the unique synthetic secret values may appear in any persisted
    # CAS artifact, including artifacts the verifier does not otherwise inspect.
    synthetic_secrets = tuple(
        value for value in forbidden if value.startswith("TOP_SECRET_")
    )
    for artifact in (data_dir / "artifacts" / "sha256").glob("*/*"):
        try:
            artifact_text = artifact.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for value in synthetic_secrets:
            if value in artifact_text:
                raise AssertionError(
                    f"synthetic secret leaked into artifact "
                    f"{artifact.relative_to(data_dir)}: {value}"
                )

    websocket_types = {
        event.get("event_type")
        for event in events
        if str(event.get("event_type", "")).startswith("websocket.")
    }
    if "websocket.created" not in websocket_types:
        raise AssertionError(f"missing WebSocket creation event: {websocket_types}")
    if not ({"websocket.frame.sent", "websocket.frame.received"} & websocket_types):
        raise AssertionError(f"missing WebSocket frame metadata: {websocket_types}")

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "events": len(events),
                "request_paths": sorted(path for path in request_paths if path),
                "action_kind": action.get("action_kind"),
                "correlation_status": link.get("correlation_status"),
                "correlation_score": link.get("correlation_score"),
                "websocket_types": sorted(websocket_types),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
