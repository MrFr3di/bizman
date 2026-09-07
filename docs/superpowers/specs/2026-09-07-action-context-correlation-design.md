# Action Context + HTTP Correlation Design

Status: approved in chat on 2026-09-07.

## Goal

Extend the passive Chrome/CDP collector with safe user-action context and deterministic action-to-HTTP correlation, while preserving immutable source observations and keeping all game-state writes outside the collector.

## Scope

This phase adds:

- a single session-level event sequencer shared by all runtime event producers;
- isolated page instrumentation for `click`, `change`, and `submit` actions;
- `Runtime.bindingCalled` ingestion and execution-context-to-frame mapping;
- a strict action normalizer that stores metadata only, never user-entered values;
- online bounded correlation between `dom.action` and first-party `http.request` events;
- immutable `correlation.action_http` link events;
- unit/privacy tests and a real Chrome for Testing E2E extension.

This phase does not add BAS integration, change detection, SQLite/Parquet state, recommendations, or write automation.

## Architectural boundaries

The collector remains an evidence pipeline, not a bot. Network events and action events are immutable observations. Correlation is represented as a separate inferred event rather than mutating an earlier HTTP record. One action may correlate to multiple requests; each request is linked to at most one best action in v1.

The runtime flow becomes:

```text
Chrome target
  |- Network.* -----------------> NetworkNormalizer --+
  |- Runtime.bindingCalled -----> ActionNormalizer ----+--> Correlator --> SessionWriter
  `- Runtime.executionContext* -> ContextRegistry -----+
```

All event producers consume a shared `EventSequencer`, so `sequence` remains globally unique and contiguous within a session.

## Chrome instrumentation

For each attached first-party page target, the orchestrator configures:

1. `Network.enable` as today.
2. `Runtime.enable` to receive execution-context lifecycle events.
3. `Runtime.addBinding` for a BizMan observer binding.
4. `Page.addScriptToEvaluateOnNewDocument` with a dedicated world when supported.

The script installs capture-phase listeners for `click`, `change`, and `submit`. When the running protocol supports `runImmediately`, the script is also installed into existing execution contexts. If not, the collector records a warning that action capture begins on the next document creation; it does not reload or navigate the page.

`executionContextName` is used for the binding when supported so the binding is scoped to the observer world. Older protocols fall back to an unscoped binding, but payload validation remains strict.

The observer never calls game APIs, prevents events, changes form values, reloads pages, or navigates.

## Safe action payload

The page-side script may emit only:

- schema version;
- action kind (`click`, `change`, `submit`);
- wall-clock timestamp and page-relative performance timestamp;
- `Event.isTrusted`;
- page pathname without query/fragment;
- element tag/type/name/role;
- a bounded selector derived only from tag plus a safe ID token;
- first-party form action pathname and normalized form method;
- bounded form field-name list.

It must never emit:

- input/textarea/select values;
- password values or password-field names after redaction;
- element text/labels;
- `innerHTML`/`outerHTML`;
- clipboard data;
- cookies, local/session storage, authorization material;
- page URL query strings or fragments;
- arbitrary `data-*` attributes.

The Python normalizer treats the binding payload as untrusted input, enforces a byte limit, validates types/enums/string lengths, re-applies field-name redaction, strips query/fragment data, and discards malformed payloads.

## Execution context mapping

`Runtime.executionContextCreated` provides an execution-context ID and `auxData.frameId`. The collector keeps a per-CDP-session `ExecutionContextRegistry` mapping context IDs to frame IDs and world names. Destroyed/cleared contexts are removed. `Runtime.bindingCalled.executionContextId` is resolved through this registry before producing a `dom.action` event.

## Time and ordering

`sequence` is a collector-wide ordering primitive and is allocated by `EventSequencer`.

`monotonic_time` becomes collector-process monotonic receipt time for new events so action and network events share one clock domain. Browser-provided Network timestamps are retained separately as `source_monotonic_time` for protocol fidelity. `http.request.observed_at` continues to use CDP `wallTime`; DOM actions use the sanitized wall time from the observer payload, falling back to collector wall time if invalid.

Correlation uses collector monotonic time as the primary temporal signal and never relies on page `performance.now()` alone.

## Correlation model

`ActionHttpCorrelator` keeps a small in-memory rolling window of recent action and request events. It is deterministic and emits a link only when the score crosses the configured minimum.

Signals:

- same target: mandatory;
- same frame when both are known: strong positive, conflicting known frames reject the pair;
- absolute collector-monotonic delta;
- form action pathname equals request pathname;
- normalized form method equals HTTP method;
- `has_user_gesture` on the request;
- trusted DOM action;
- submit action receives a small preference over generic click when form metadata matches.

Statuses:

- `strong`: score >= 0.80;
- `probable`: score >= 0.60;
- `temporal-only`: score >= 0.40 and no structural path/method match;
- below 0.40: no persisted link.

`exact` is reserved for a future explicit causal identifier from controlled BAS/experiment execution and is not produced by DOM/CDP heuristics.

A correlation event contains `action_event_id`, `network_event_id`, `correlation_status`, `correlation_score`, `delta_ms`, and the exact signal names used. Its source is `system` and confidence is `inferred`.

## Event schema changes

`schemas/event.schema.json` gains explicit optional contracts for:

- `source_monotonic_time`;
- action metadata fields;
- correlation link fields and score/status/signals.

The schema remains Draft 2020-12 and backward compatible for existing normalized network events.

## Safety

The command allowlist is renamed from passive-only wording to observation/instrumentation wording and permits only the minimum additional CDP methods required for action observation. It still excludes navigation, DOM mutation, request interception, storage/cookie access, form submission, and arbitrary page evaluation.

No LLM runs in the collection or correlation hot path. Ornith/Luna/Sol are downstream consumers only after deterministic novelty/change detection in later phases.

## Verification

Unit tests cover:

- shared sequence uniqueness across producers;
- strict binding payload parsing and privacy redaction;
- execution-context lifecycle mapping;
- page instrumentation capability fallbacks;
- correlation scoring, rejection, multi-request-per-action behavior, and duplicate suppression;
- malformed/spoofed binding calls.

The Chrome E2E fixture is extended with synthetic click/submit actions and must prove:

- at least one `dom.action` event is persisted;
- at least one `correlation.action_http` event links a synthetic user action to `/api/post`;
- synthetic input secrets do not appear in JSONL or artifacts;
- existing HTTP/WebSocket privacy assertions still pass.
