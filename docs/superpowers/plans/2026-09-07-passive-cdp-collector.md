# BizMan Passive CDP Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a passive, version-aware Chrome/CDP collector that records sanitized first-party BizMania network metadata into local append-only session storage.

**Architecture:** Discover the exact running Chrome protocol, connect to the browser-level WebSocket, use flattened target sessions, normalize Network events into the existing event envelope, and persist only sanitized local JSONL/CAS artifacts. The collector remains read-only and operational data stays outside Git.

**Tech Stack:** Python 3.11+, preferred Python 3.14, `asyncio.TaskGroup`, `websockets==17.1`, stdlib `urllib`, JSONL, SHA-256.

**Spec:** `docs/superpowers/specs/2026-09-07-passive-cdp-collector-design.md`

## Global Constraints

- No GitHub Actions runs are required for this phase; CI remains manual-only.
- No browser cookie/storage export, cache mutation, service-worker bypass, page mutation or BizMania write automation.
- Persist first-party events only by default.
- Persist request bodies only when structurally sanitized and size/MIME policy allows them.
- Response bodies remain metadata-only in this phase.

---

### Task 1: Discovery and local storage contracts

**Files:**
- Create: `tools/bizman_collector/discovery.py`
- Create: `tools/bizman_collector/storage.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `BrowserDiscovery`, `discover_browser(...)`, `ArtifactStore.put_bytes(...)`, `SessionWriter`.

- [ ] Write failing tests for `/json/version` parsing, protocol fingerprinting, CAS deduplication and append-only event writing.
- [ ] Implement stdlib discovery and atomic local storage.
- [ ] Run collector tests.

### Task 2: CDP connection core

**Files:**
- Create: `tools/bizman_collector/cdp.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `CdpConnection`, `CdpEvent`, `CdpProtocolError`, `open_cdp_connection(...)`.

- [ ] Write failing async tests for command/response correlation, event delivery and protocol errors with an in-memory fake transport.
- [ ] Implement one receiver loop, globally unique command IDs and pending-future cleanup on close.
- [ ] Use `websockets.asyncio.client.connect` only in the production transport adapter.

### Task 3: First-party request normalization

**Files:**
- Create: `tools/bizman_collector/network.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `FirstPartyPolicy`, `NetworkNormalizer.normalize(...)`, structured request-body sanitization.

- [ ] Write failing tests for third-party filtering, URL path/query normalization, header redaction and JSON/form body sanitization.
- [ ] Implement request/response/loading/WebSocket metadata normalization.
- [ ] Persist body bytes only through `ArtifactStore` and return `sha256:` refs.

### Task 4: Flattened target-session orchestration

**Files:**
- Create: `tools/bizman_collector/targets.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `TargetRegistry`, `TargetOrchestrator.bootstrap()`, `TargetOrchestrator.handle_event(...)`.

- [ ] Write failing tests proving existing BizMania pages are attached with `flatten=true`, Network is enabled per session, related targets use `Target.setAutoAttach`, and new page targets are attached without duplication.
- [ ] Implement runtime target/session mappings and recursive child registration.

### Task 5: Collector runtime and CLI

**Files:**
- Create: `tools/bizman_collector/runtime.py`
- Create: `tools/collect_live.py`
- Modify: `tools/requirements.txt`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- CLI: `python tools/collect_live.py --endpoint http://127.0.0.1:9222 --data-dir <path> --host bizmania.ru`.

- [ ] Pin `websockets==17.1`.
- [ ] Compose discovery, CDP receiver/event loop, target orchestration and session writer with `asyncio.TaskGroup`.
- [ ] Finalize `ended_at` on normal exit/Ctrl+C.
- [ ] Document dedicated Chrome profile/remote-debugging launch requirements without enabling any automatic CI trigger.

### Task 6: Local verification

- [ ] Run `python -m unittest discover -s tests -v` locally.
- [ ] Run `python tools/validate_repo.py` on the available local fixture/checkout.
- [ ] Inspect changed filenames for forbidden operational artifacts.
- [ ] Do not dispatch GitHub Actions unless explicitly requested by the repository owner.
