#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs

from jsonschema import Draft202012Validator, FormatChecker

from bizman.current import rebuild_current_state
from bizman.foundation.redaction import load_redaction_policy

ACTION_POST_PATH = "/api/action-post"
COMPANY_ROSTER_PATH = "/company/"


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


def _assert_no_artifact_secrets(
    data_dir: Path,
    secrets: tuple[str, ...],
) -> None:
    encoded = tuple((value, value.encode("utf-8")) for value in secrets)
    artifact_root = data_dir / "artifacts" / "sha256"
    if not artifact_root.exists():
        return
    for artifact in artifact_root.glob("*/*"):
        artifact_bytes = artifact.read_bytes()
        for value, needle in encoded:
            if needle in artifact_bytes:
                raise AssertionError(
                    f"synthetic secret leaked into artifact "
                    f"{artifact.relative_to(data_dir)}: {value}"
                )


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
    for required in {
        "/api/get",
        "/api/post",
        "/redirect",
        ACTION_POST_PATH,
        COMPANY_ROSTER_PATH,
    }:
        if required not in request_paths:
            raise AssertionError(f"missing expected request path {required}; got {sorted(request_paths)}")

    roster_bodies = [
        event
        for event in events
        if event.get("event_type") == "http.response_body"
        and event.get("url_path") == COMPANY_ROSTER_PATH
        and event.get("query", {}).get("id") == ["13393"]
        and event.get("query", {}).get("tab") == ["units"]
    ]
    if len(roster_bodies) != 1:
        raise AssertionError(
            "expected one company roster body event, "
            f"found {len(roster_bodies)}; warnings={manifest.get('warnings', [])}"
        )
    roster_ref = roster_bodies[0].get("response_body_ref")
    if not isinstance(roster_ref, str):
        raise AssertionError("company roster response artifact is missing")
    roster_artifact = json.loads(
        _artifact_path(data_dir, roster_ref).read_text(encoding="utf-8")
    )
    if roster_artifact.get("sanitizer_version") != 2:
        raise AssertionError("unexpected company roster sanitizer version")
    roster_text = roster_artifact.get("text")
    if not isinstance(roster_text, str):
        raise AssertionError("company roster sanitized text is missing")
    for required_text in (
        "Компания Paradise",
        "Детский магазин #33670",
        "Аптека #33676",
    ):
        if required_text not in roster_text:
            raise AssertionError(
                f"sanitized company roster is missing {required_text!r}"
            )

    post_event = _request_for_path(events, "/api/post")
    body_ref = post_event.get("request_body_ref")
    if not isinstance(body_ref, str):
        raise AssertionError("sanitized JSON POST body was not persisted")
    body = _artifact_path(data_dir, body_ref).read_text(encoding="utf-8")
    if json.loads(body) != {"safe": "kept"}:
        raise AssertionError(f"unexpected sanitized POST artifact: {body}")

    action_post = _request_for_path(events, ACTION_POST_PATH)
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
        and event.get("form_action_path") == ACTION_POST_PATH
    ]
    if not submit_actions:
        raise AssertionError(f"missing normalized DOM submit action for {ACTION_POST_PATH}")
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
        "TOP_SECRET_ROSTER_INPUT",
        "TOP_SECRET_ROSTER_SCRIPT",
        "TOP_SECRET_ROSTER_HIDDEN_NESTED",
        "TOP_SECRET_ROSTER_HIDDEN_TAIL",
        "TOP_SECRET_ROSTER_HIDDEN_STYLE",
        "TOP_SECRET_ROSTER_HIDDEN_ARIA",
        "clientSecret",
        "accessToken",
    )
    for value in forbidden:
        if value in serialized_events or value in body or value in action_body:
            raise AssertionError(f"secret leaked into durable output: {value}")

    synthetic_secrets = tuple(
        value for value in forbidden if value.startswith("TOP_SECRET_")
    )
    _assert_no_artifact_secrets(data_dir, synthetic_secrets)

    redaction = load_redaction_policy(
        args.repo_root / "config" / "redaction-policy.json"
    )
    first_snapshot = rebuild_current_state(
        args.repo_root,
        data_dir,
        redaction,
    )
    if [
        (item.company_id, item.name)
        for item in first_snapshot.companies
    ] != [("13393", "Paradise")]:
        raise AssertionError(
            f"unexpected company projection: {first_snapshot.companies}"
        )
    if [
        (item.unit_id, item.company_id, item.city_name, item.level)
        for item in first_snapshot.units
    ] != [
        ("33670", "13393", "Анкара", 1),
        ("33676", "13393", "Анкара", 1),
    ]:
        raise AssertionError(
            f"unexpected unit projection: {first_snapshot.units}"
        )

    state_path = data_dir / "state" / "current.sqlite3"
    first_fingerprint = first_snapshot.metadata.state_fingerprint
    state_path.unlink()
    state_path.with_name(state_path.name + "-wal").unlink(missing_ok=True)
    state_path.with_name(state_path.name + "-shm").unlink(missing_ok=True)
    second_snapshot = rebuild_current_state(
        args.repo_root,
        data_dir,
        redaction,
    )
    if second_snapshot != first_snapshot:
        raise AssertionError("Current State delete/replay changed semantic snapshot")
    if second_snapshot.metadata.state_fingerprint != first_fingerprint:
        raise AssertionError("Current State delete/replay changed state fingerprint")

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
                "current_state_companies": len(first_snapshot.companies),
                "current_state_units": len(first_snapshot.units),
                "current_state_fingerprint": first_fingerprint,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
