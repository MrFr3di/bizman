# Action Context + HTTP Correlation Design

Status: approved in chat on 2026-09-07; security hardening incorporated during PR review.

## Goal

Extend the passive Chrome/CDP collector with safe user-action context and deterministic action-to-HTTP correlation, while preserving immutable source observations and keeping all game-state writes outside the collector.

## Scope

This phase adds:

- a single session-level event sequencer shared by all runtime event producers;
- isolated page instrumentation for `click`, `change`, and `submit` actions;
- `Runtime.bindingCalled` ingestion and execution-context-to-frame/world/origin mapping;
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

All event producers consume a shared `EventSequencer`, so `sequence` remains globally unique and contiguous within a session. Source action/request observations reach the correlator in strictly increasing sequence order; the correlator uses that invariant for O(1)-memory replay rejection while keeping only a bounded recent candidate window.

## Chrome instrumentation

For each attached first-party page or iframe target, the orchestrator configures:

1. `Network.enable` as today.
2. `Runtime.enable` to receive execution-context lifecycle events.
3. `Runtime.addBinding` scoped with `executionContextName` to the BizMan observer world.
4. `Page.addScriptToEvaluateOnNewDocument` with the same dedicated `worldName`.

Secure action observation requires both `Runtime.addBinding.executionContextName` and `Page.addScriptToEvaluateOnNewDocument.worldName`, plus the Runtime context lifecycle/binding events used by the Python guard. If the running protocol cannot prove those capabilities, DOM action observation is disabled and the session manifest receives an explicit warning. There is intentionally no unscoped main-world fallback.

When the running protocol supports `runImmediately`, the script is also installed into existing execution contexts. If not, the collector records a warning that action capture begins on a future document creation; it does not reload or navigate the page.

The Python ingestion boundary accepts a binding call only when the execution context belongs to the configured observer world and its origin is first-party. Browser-side hostname gating is defense in depth, not the trust boundary.

The observer never calls game APIs, prevents events, changes form values, reloads pages, or navigates.

## Safe action payload

The page-side script may emit only:

- schema version;
- action kind (`click`, `change`, `submit`);
- wall-clock timestamp and page-relative performance timestamp;
- `Event.isTrusted` as a boolean;
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

The Python normalizer treats the binding payload as untrusted input, enforces a byte limit, validates types/enums/string lengths, re-applies field-name redaction, strips query/fragment data, and discards malformed payloads. Non-boolean `isTrusted` values are treated as untrusted and cannot influence correlation scoring.

## Execution context and target lifecycle

`Runtime.executionContextCreated` provides an execution-context ID and `auxData.frameId`. The collector keeps a per-CDP-session `ExecutionContextRegistry` mapping context IDs to frame IDs, world names, and origins. Destroyed/cleared contexts are removed. `Target.detachedFromTarget`, `Target.targetDestroyed`, and `Target.targetCrashed` clear associated target/session configuration and execution-context state so stale origins cannot survive terminal target lifecycle events.

Unknown child targets with an empty URL may receive passive Network/Target setup, but DOM observation is deferred until a later target update proves a first-party URL. Explicit third-party targets receive no DOM instrumentation.

## Time and ordering

`sequence` is a collector-wide ordering primitive and is allocated by `EventSequencer`.

`monotonic_time` is collector-process monotonic receipt time so action and network events share one clock domain. Browser-provided Network timestamps are retained separately as `source_monotonic_time` for protocol fidelity. `http.request.observed_at` continues to use CDP `wallTime`; DOM actions use the sanitized wall time from the observer payload, falling back to collector wall time if invalid.

Correlation uses collector monotonic time as the primary temporal signal and never relies on page `performance.now()` alone.

## Correlation model

`ActionHttpCorrelator` keeps a bounded in-memory rolling window of recent action and request events. It is deterministic and emits a link only when the score crosses the configured minimum.

Signals:

- same target: mandatory;
- same frame when both are known: strong positive; conflicting known frames reject the pair;
- absolute collector-monotonic delta;
- form action pathname equals request pathname;
- normalized form method equals HTTP method;
- `has_user_gesture` on the request;
- trusted DOM action;
- submit action receives a small preference when form path and method both match.

A method match is supporting evidence only because unrelated requests often share GET/POST. `probable` or `strong` therefore requires at least a form-path match or explicit request user-gesture signal; method-only matches remain at most `temporal-only`.

Statuses:

- `strong`: score >= 0.80 with causal evidence;
- `probable`: score >= 0.60 with causal evidence;
- `temporal-only`: score >= 0.40 without sufficient causal evidence;
- below 0.40: no persisted link.

`exact` is reserved for a future explicit causal identifier from controlled BAS/experiment execution and is not produced by DOM/CDP heuristics.

A correlation event contains `action_event_id`, `network_event_id`, `correlation_status`, `correlation_score`, `delta_ms`, and the exact signal names used. Its source is `system` and confidence is `inferred`.

## Network persistence hardening

Network evidence remains first-party-only. Secret-named headers and authorization/cookie families are dropped before persistence. URL-bearing headers such as `Referer`, `Location`, `Link`, `Refresh`, `Content-Location`, and `Sec-WebSocket-Protocol` are not persisted because their values can carry query/auth material and equivalent safe path metadata is already represented structurally.

Request bodies are persisted only for supported structured formats under the configured byte cap. JSON persistence is limited to objects or lists of objects that can be recursively redacted by field name; top-level scalars and unstructured scalar arrays remain metadata-only. WebSocket frame payloads are never persisted.

## Event schema changes

`schemas/event.schema.json` gains explicit optional contracts for:

- `source_monotonic_time`;
- action metadata fields;
- correlation link fields and score/status/signals.

The schema remains Draft 2020-12 and backward compatible for existing normalized network events.

## Safety

The CDP command allowlist permits only the minimum observation/instrumentation methods required for this phase. It excludes navigation, DOM mutation, request interception, storage/cookie access, form submission, and arbitrary page evaluation.

No LLM runs in the collection or correlation hot path. Ornith/Luna/Sol are downstream consumers only after deterministic novelty/change detection in later phases.

## Verification

Unit/deep-regression tests cover:

- shared sequence uniqueness and replay rejection;
- strict binding payload parsing and privacy redaction;
- isolated-world and first-party execution-context guards;
- execution-context and terminal target lifecycle cleanup;
- secure capability requirements and disabled-observer warnings;
- retryable action instrumentation after transient CDP errors;
- correlation scoring, causal-evidence thresholds, rejection, multi-request-per-action behavior, bounded state, and duplicate suppression;
- sensitive header and structured-body persistence rules;
- byte-level CAS secret scanning.

The Chrome E2E fixture must prove:

- at least one `dom.action` event is persisted from a real browser form submission;
- at least one `correlation.action_http` event links that action to `/api/action-post`;
- synthetic input/query/body/WebSocket secrets do not appear in JSONL or any CAS artifact;
- existing HTTP/redirect/WebSocket privacy assertions still pass.
