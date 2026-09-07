# Action Context + HTTP Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe DOM action observation and deterministic action-to-HTTP correlation to the existing passive Chrome/CDP collector.

**Architecture:** Network and DOM observations remain immutable events. A shared session sequencer provides global ordering; `Runtime.bindingCalled` is normalized into `dom.action`; a bounded deterministic correlator emits separate `correlation.action_http` inferred events. Chrome instrumentation is capability-detected and never navigates or submits game state.

**Tech Stack:** Python 3.14, asyncio, websockets 17.1, Chrome DevTools Protocol discovered at runtime, JSON Schema Draft 2020-12, unittest, Chrome for Testing GitHub Actions E2E.

**Spec:** `docs/superpowers/specs/2026-09-07-action-context-correlation-design.md`

## Global Constraints

- Never persist user-entered values, cookies, storage, auth material, query secrets, element text, HTML, or clipboard content.
- No LLM in collector/correlator hot path.
- No navigation/reload/form-submission/request-interception CDP commands.
- Existing HTTP/WebSocket privacy behavior remains unchanged.
- `exact` correlation is not emitted by heuristics.
- Operational BizManData remains outside Git.
- PR validation continues to use real Chrome E2E and no routine push/scheduled CI.

---

### Task 1: Shared event sequencing and common collector clock

**Files:**
- Create: `tools/bizman_collector/events.py`
- Modify: `tools/bizman_collector/network.py`
- Modify: `tools/bizman_collector/runtime.py`
- Test: `tests/test_event_sequencing.py`

**Interfaces:**
- Produces: `EventSequencer.next() -> int`
- Produces: `CollectorClock.monotonic() -> float`, `CollectorClock.wall_iso() -> str`
- `NetworkNormalizer(..., sequencer: EventSequencer, clock: CollectorClock)`

- [ ] **Step 1: Write failing tests**

Test that one `EventSequencer` returns `0, 1, 2` across independent callers and rejects no normal use. Test that two NetworkNormalizer instances sharing one sequencer cannot emit duplicate `sequence`. Test that an HTTP request retains CDP `timestamp` as `source_monotonic_time` while canonical `monotonic_time` comes from the injected collector clock.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_event_sequencing -v
```

Expected: failure because `events.py` and the new constructor parameters do not exist.

- [ ] **Step 3: Implement minimal sequencing/clock support**

Create:

```python
class EventSequencer:
    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        value = self._value
        self._value += 1
        return value


class CollectorClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall_iso(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
```

Inject both into `NetworkNormalizer`. Replace its private counter. Persist valid CDP `params.timestamp` as `source_monotonic_time`; use collector monotonic time for `monotonic_time`. Preserve CDP `wallTime` for `http.request.observed_at` and current behavior for other network events.

- [ ] **Step 4: Run focused and existing collector tests**

```bash
python -m unittest tests.test_event_sequencing tests.test_collector tests.test_collector_hardening -v
```

Expected: all pass.

---

### Task 2: Safe DOM action observer and Runtime binding normalization

**Files:**
- Create: `tools/bizman_collector/action_script.py`
- Create: `tools/bizman_collector/actions.py`
- Modify: `schemas/event.schema.json`
- Test: `tests/test_action_observer.py`

**Interfaces:**
- Produces: `build_action_observer_script(binding_name: str) -> str`
- Produces: `ExecutionContextRegistry.register(session_id, context_id, frame_id, world_name)` and lifecycle removal methods
- Produces: `ActionNormalizer.normalize_binding(payload: str, *, target_id: str | None, frame_id: str | None) -> dict | None`

- [ ] **Step 1: Write failing privacy and contract tests**

Cover:

- valid click payload becomes `dom.action`;
- submit payload stores first-party `form_action_path`, uppercase method, safe field names;
- `password`, `token`, `clientSecret` field names are dropped;
- values, text, HTML and arbitrary extra keys are never copied even if injected into a spoofed payload;
- oversized or malformed JSON returns `None`;
- unknown action kind returns `None`;
- third-party form action is not persisted;
- generated script source contains no value extraction (`.value`), `innerHTML`, `outerHTML`, cookie/storage/clipboard APIs.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_action_observer -v
```

Expected: module/import failures.

- [ ] **Step 3: Implement the observer script**

The script must install capture-phase listeners for `click`, `change`, `submit`; construct a fixed-shape payload; use `location.pathname`; derive selector only from lower-case tag and a safe bounded ID token; collect only bounded element/form metadata; call exactly one string-argument CDP binding; never prevent/default-stop events.

- [ ] **Step 4: Implement strict Python normalization and context registry**

Parse at most 16 KiB. Accept only schema `1`, kinds `click/change/submit`, bounded strings, booleans, numeric times, and a maximum 128 form field names. Re-run `RedactionPolicy.should_drop_field`. Normalize first-party form action through `FirstPartyPolicy`; store pathname only. Allocate `event_id`, shared `sequence`, collector `monotonic_time`, sanitized `observed_at`, `source="dom.action"`, `confidence="observed"`, and deterministic fingerprint.

- [ ] **Step 5: Extend event schema explicitly**

Add optional definitions for `source_monotonic_time`, `action_kind`, `is_trusted`, `page_path`, `element_tag`, `element_type`, `element_name`, `element_role`, `safe_selector`, `form_action_path`, `form_method`, and `form_field_names` with bounded lengths/items.

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_action_observer -v
```

Expected: all pass.

---

### Task 3: Deterministic action-to-HTTP correlator

**Files:**
- Create: `tools/bizman_collector/correlation.py`
- Modify: `schemas/event.schema.json`
- Test: `tests/test_correlation.py`

**Interfaces:**
- Produces: `ActionHttpCorrelator.observe(event: dict) -> list[dict]`
- Consumes shared `EventSequencer` and `CollectorClock`

- [ ] **Step 1: Write failing scoring tests**

Cover:

- different target IDs never correlate;
- conflicting known frames never correlate;
- matching submit + form path + method + close time yields `strong`;
- close trusted click + user gesture without structural form match can yield `probable` or `temporal-only`, never `exact`;
- stale actions outside the rolling window are ignored;
- one action can link multiple HTTP requests;
- one request receives at most one best link;
- a later-arriving action can correlate to a recent unlinked request;
- repeated observation never emits the same pair twice.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_correlation -v
```

Expected: missing correlator module.

- [ ] **Step 3: Implement bounded deterministic scoring**

Maintain recent action/request deques and linked request/pair sets. Default window: 2 seconds. Score with explicit named signals:

```text
same_target             required
same_frame              +0.25; conflicting known frame => reject
<=150 ms                +0.30
<=500 ms                +0.22
<=1500 ms               +0.10
form_path_match         +0.25
method_match            +0.15
request_user_gesture    +0.10
trusted_action          +0.05
submit_structural_bonus +0.05
```

Cap score at `1.0`. Emit `strong >= .80`, `probable >= .60`, `temporal-only >= .40`; below `.40` no event. Use absolute collector-monotonic delta so small CDP delivery reordering can still correlate.

- [ ] **Step 4: Emit immutable correlation events**

Set `source="system"`, `event_type="correlation.action_http"`, `confidence="inferred"`, new event/session/sequence timestamps, `action_event_id`, `network_event_id`, `correlation_status`, rounded `correlation_score`, `delta_ms`, sorted unique `signals`, and deterministic fingerprint.

- [ ] **Step 5: Extend schema and run tests**

```bash
python -m unittest tests.test_correlation -v
```

Expected: all pass.

---

### Task 4: Wire Runtime/Page instrumentation into target orchestration

**Files:**
- Modify: `tools/bizman_collector/targets.py`
- Modify: `tools/bizman_collector/runtime.py`
- Modify: `tools/bizman_collector/discovery.py` only if existing capability helpers cannot inspect the needed parameters
- Test: `tests/test_action_runtime.py`

**Interfaces:**
- Runtime allowlist adds only: `Runtime.enable`, `Runtime.addBinding`, `Page.addScriptToEvaluateOnNewDocument`.
- `TargetOrchestrator` accepts action script/binding configuration and configures each first-party attached session.
- `_consume_events` handles `Runtime.executionContext*` and `Runtime.bindingCalled` in addition to existing Target/Network events.

- [ ] **Step 1: Write failing orchestration tests**

Assert command ordering per attached session: Network enable, Runtime enable, Runtime binding, Page new-document script. Assert `executionContextName` and `runImmediately` are sent only when supported by the discovered protocol. Assert fallback warnings when unavailable. Assert no forbidden command is emitted.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_action_runtime -v
```

Expected: current allowlist/orchestrator lacks action instrumentation.

- [ ] **Step 3: Implement capability-aware instrumentation**

Use a constant observer world name and binding name. Prefer scoped binding via `executionContextName` if supported. Prefer isolated world via `worldName` if supported. Use `runImmediately=True` when supported; otherwise install for future documents and emit a warning. Do not add `Runtime.evaluate`, `Page.reload`, `Page.navigate`, or DOM mutation methods.

- [ ] **Step 4: Wire action normalization and correlator into the runtime**

Create one shared sequencer/clock/context registry/correlator per collection session. For every normalized network or DOM action event, append the observation then feed it to the correlator and append any returned correlation events. Handle context create/destroy/clear events without persisting them unless needed for warnings.

- [ ] **Step 5: Run all unit/contract tests**

```bash
python -m unittest discover -s tests -v
python tools/validate_repo.py
```

Expected: all pass.

---

### Task 5: Extend real Chrome E2E and documentation

**Files:**
- Modify: `tools/ci/fixture_server.py`
- Modify: `tools/ci/verify_collector_e2e.py`
- Modify: `README.md`
- Modify: `docs/CI.md`
- Modify: `docs/architecture/storage.md` only if event semantics there need clarification

**Interfaces:**
- Fixture adds a synthetic form/button with secret input value and deterministic first-party POST.
- E2E verifier asserts action/correlation presence and secret absence.

- [ ] **Step 1: Extend fixture**

Create a real user-clickable button/form whose event path triggers `/api/post` and includes a synthetic secret input value such as `TOP_SECRET_INPUT_VALUE`. Preserve the existing network/WebSocket fixture coverage.

- [ ] **Step 2: Extend verifier**

Require at least one `dom.action` and one `correlation.action_http` whose linked network event points to `/api/post`. Scan serialized events plus all small text artifacts used by the fixture and fail if the synthetic input value or any existing synthetic secret appears.

- [ ] **Step 3: Run full PR quality gate through GitHub**

Open the PR only after local-contract changes are complete. Required jobs:

- `validate` success;
- `collector-e2e` success;
- benchmark success/non-regression;
- CodeRabbit/status review contains no blocker.

- [ ] **Step 4: Update docs**

Document action metadata privacy, separate correlation-link events, current no-LLM hot path, and the next phase as change detection/promotion bundles.

- [ ] **Step 5: Merge only from the verified PR head**

Use the expected head SHA when merging so GitHub rejects a moved head.
