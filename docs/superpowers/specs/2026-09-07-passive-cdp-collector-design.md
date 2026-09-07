# BizMan Passive CDP Collector Design

## Goal

Continuously capture BizMania browser-network observations from a dedicated Chrome debugging profile and write sanitized, append-only local session data that can later be normalized, diffed and promoted into `knowledge/`.

## Scope

This phase is passive and read-only. It connects to an already-running Chrome instance; it does not click, submit forms, alter cache/service-worker behavior, execute BizMania write actions, update SQLite/Parquet, or create Git pull requests.

## Runtime

- Python 3.11+; Python 3.14 is the preferred runtime.
- `asyncio.TaskGroup` for structured concurrency.
- `websockets==17.1` using `websockets.asyncio.client`; the legacy websockets API is not used.
- Stdlib HTTP client for the local DevTools discovery endpoints.
- Chrome must use a dedicated non-default `--user-data-dir` when remote debugging is enabled.

## Protocol compatibility

The collector never assumes that the Chrome DevTools Protocol tip-of-tree is stable. On every session it reads:

- `/json/version` for browser metadata and the browser WebSocket URL;
- `/json/protocol` for the exact protocol definition spoken by the running browser.

The protocol document is fingerprinted and stored locally as a content-addressed artifact. The collector uses browser-level CDP with flattened target sessions.

## Target model

The browser connection enables target discovery, attaches to existing first-party page targets, and listens for new/changed targets. Each attached page session enables the Network domain and enables flattened auto-attach for related targets. Child targets are registered recursively when `Target.attachedToTarget` is observed.

The collector does not pause targets (`waitForDebuggerOnStart=false`). Target/session mappings are runtime state only.

## Network capture

Phase 2B records normalized metadata for:

- `Network.requestWillBeSent`;
- `Network.responseReceived`;
- `Network.loadingFinished`;
- `Network.loadingFailed`;
- WebSocket create/handshake/frame/close events.

The normalized envelope uses the existing `schemas/event.schema.json`: UUIDv7 event/session IDs, per-session sequence, CDP monotonic timestamp, request/loader/frame IDs, URL path, method/status and source/confidence.

Only first-party BizMania URLs are persisted by default. Third-party analytics/network noise is ignored.

## Redaction and bodies

Headers are redacted before persistence. Request bodies are persisted only when all of the following are true:

1. the URL is first-party;
2. the body is within `max_request_bytes`;
3. the content type is allowlisted;
4. the body can be structurally sanitized (JSON or form-urlencoded in this phase).

Unknown/unstructured request bodies and response bodies are metadata-only in this phase. Full forensic capture is intentionally out of scope.

Sanitized bodies and the protocol document are stored in a local SHA-256 content-addressed artifact store. Event JSONL stores only `sha256:<digest>` references.

## Storage

Default local root is `~/BizManData`, overridable by CLI. A collection session writes:

```text
BizManData/
  sessions/<session_id>/manifest.json
  events/YYYY-MM-DD/<session_id>.jsonl
  artifacts/sha256/<prefix>/<digest>
```

Writes are append-only for event JSONL and atomic for manifests/artifacts. Nothing under this local root is committed to Git.

## Failure semantics

- CDP command errors are surfaced as typed errors and do not silently become successful observations.
- Connection closure fails outstanding commands.
- Unsupported Network/Target commands on a child target are reported as local system observations where practical; they do not cause state-changing browser actions.
- Ctrl+C closes the collector and finalizes `ended_at` in the session manifest.

## Security boundary

The collector never calls cookie APIs, never exports browser storage state and never persists Authorization/Cookie/Set-Cookie headers. It does not disable cache, bypass service workers or mutate the page/browser.

## Deferred

DOM action instrumentation, BAS correlation, response-body/HTML normalization, change detection, SQLite projections, Parquet history, experiments and write automation remain separate later phases.
