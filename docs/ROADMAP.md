# BizMan unified roadmap

Status: architecture roadmap reviewed and consolidated on 2026-09-07.

This document combines the existing BizMan plan with a critical review of the proposed Agent Toolchain/MCP direction. It is intentionally sequenced around the code that already exists: passive capture and action-to-HTTP correlation are in `main`, while PR #4 is already implementing the deterministic Change Detector + Promotion Bundle pipeline. We do **not** restart the project or renumber the current work to fit a hypothetical greenfield plan.

## 1. Executive direction

BizMan should evolve into a deterministic evidence/state platform with a small read-optimized agent surface on top:

```text
Chrome / user activity
        |
        v
Passive CDP Collector
        |
        v
immutable sanitized JSONL + CAS
        |
        +-----------------------------+
        |                             |
        v                             v
Change Detector                 State/History derivations
        |                             |
        v                             v
Promotion Bundles          SQLite current + Parquet history
        |                             |
        +-------------+---------------+
                      |
                      v
                BizMan Core API
                      |
         +------------+-------------+
         |                          |
         v                          v
      CLI/read API          read-only MCP adapter
                                    |
                    +---------------+---------------+
                    |               |               |
                  Codex          ChatGPT       other MCP hosts
```

The central rule is unchanged: **immutable sanitized evidence is the source of truth; every DB/index is rebuildable; no LLM decides what was observed.**

The main architectural addition is a compact **BizMan Core API** and an **agent read model** so agents stop opening JSON/JSONL directly for ordinary tasks.

## 2. Decisions from the external proposal

### Adopt

The following proposals fit the existing architecture and should become target design:

1. Separate domain/core services from MCP handlers.
2. Migrate production Python code to a normal package under `src/` after PR #4 is complete.
3. Adopt `pyproject.toml` + `uv.lock` for deterministic environments.
4. Use the official MCP Python SDK 2.x rather than building a custom protocol layer.
5. Start MCP with **stdio only**; defer HTTP/OAuth/remote deployment.
6. Build a read-optimized agent index instead of letting agents scan `knowledge/**/*.json*` and session JSONL.
7. Use exact stable IDs/aliases first, then SQLite FTS5/BM25; add embeddings only after retrieval eval proves a gap.
8. Use a resolve -> stable ref -> query workflow similar to Context7.
9. Keep the MCP tool surface small and profile-specific.
10. Enforce output budgets, pagination and progressive disclosure in code.
11. Return references/provenance by default rather than full documents.
12. Use MCP resources for large read objects, with tool fallbacks for clients with weak resource support.
13. Prefer high-level evidence/session operations such as `evidence.trace` over primitive file-style tools.
14. Build deterministic session summaries/anomaly indexes so models do not sit on raw logs.
15. Finish Change Detection before the State projector.
16. Make the State projector fully replayable/versioned.
17. Use SQLite for current/read state, Parquet for history, DuckDB for analytical queries.
18. Keep the common path tool-first; specialist agents are optional escalation, not the default architecture.
19. Keep read and future write MCP processes physically separate.
20. Do not build v1 around MCP Tasks; the Python SDK roadmap still lists the 2026 Tasks extension as not implemented.
21. Use stderr for stdio logs; consider OpenTelemetry later for structured metrics.
22. Create an explicit agent/retrieval benchmark instead of assuming MCP reduces tokens.

### Adopt with changes

#### Package migration timing

Do **not** stop or rewrite current PR #4 to move everything into `src/`. PR #4 has already established and begun testing a versioned Change Detector contract. A cross-cutting package migration now would make detector correctness and import migration fail in the same diff.

Target order:

```text
PR #4  Change Detector + Promotion Bundle
        ↓
PR #5  Python package/Core foundation migration
```

#### Python support

Do not set `requires-python = ">=3.14"` yet.

Current code deliberately supports pre-3.14 environments (for example the UUIDv7 fallback), and `asyncio.TaskGroup`/`StrEnum` make Python 3.11 a natural floor. The official MCP Python SDK 2.0 itself supports Python >=3.10.

Target:

```toml
requires-python = ">=3.11"
```

CI policy after packaging:

- Python 3.14: primary/full validation.
- Python 3.11: lightweight compatibility/import/unit lane where practical.

Python 3.14 remains preferred for production/dev, but compatibility should not be removed without evidence that it materially simplifies the system.

#### Dependency strategy

`pyproject.toml` should contain compatibility ranges; `uv.lock` is the exact reproducibility boundary.

Do **not** add all future dependencies in the packaging PR.

Initial package dependencies should remain close to what production already needs:

```text
websockets 17.x
jsonschema 4.x
```

Add only when the corresponding feature ships:

- `mcp 2.x` -> MCP PR.
- DuckDB -> history/analytics PR.
- Pydantic -> only if the MCP/domain boundary actually benefits from direct project models; the official MCP SDK already uses Pydantic internally, so adding Pydantic to BizMan core before a concrete use is unnecessary.

Ruff belongs in a development dependency group, not runtime.

#### MCP version pinning

Use the official MCP Python SDK v2 line, but do not hardcode architectural assumptions to exactly `2.0.0`. v2 is current stable and implements MCP `2026-07-28`, but there are active SDK issues and subsequent 2.x releases.

Policy:

```text
pyproject: mcp >=2,<3
uv.lock: exact tested release
CI: MCP contract/in-memory/stdio smoke tests
upgrade: explicit, reviewed lock update
```

#### One DB vs multiple DBs

Reject one monolithic `BizManData/database/bizman.sqlite` as the first implementation.

The detector, search index and current-state projector have different rebuild/version lifecycles. Physical separation reduces migration coupling and blast radius:

```text
BizManData/
  detector/state.sqlite3       detector checkpoints/findings/outbox
  index/agent-index.sqlite3    refs/search/session summaries/read index
  state/current.sqlite3        replayable current domain state
  history/...                  Parquet history
```

BizMan Core can aggregate these stores behind repositories/services. A later benchmark can justify consolidation if cross-DB cost becomes material.

#### Tool count and profiles

Do not encode “16 total tools” or “<10 per namespace” as a protocol law. OpenAI Tool Search exists specifically to defer large tool surfaces, but not every MCP host supports the same mechanism.

Host-neutral rule:

- default profile: <= 8-10 exposed tools;
- total catalog can be larger if profiles keep the active surface small;
- tool count is finalized by agent benchmark, not aesthetics.

#### OpenAI Tool Search

Tool Search/deferred loading is an optional host optimization, not a BizMan architectural dependency. BizMan must remain efficient in Codex/ChatGPT/other MCP clients that expose the entire active profile.

#### MCP annotations

Use `readOnlyHint=true` and, for local derived-data tools, normally `openWorldHint=false`. Treat annotations strictly as metadata/hints; authorization and safety remain implementation invariants.

### Reject or defer

Do not add now:

- FastAPI/REST server;
- Streamable HTTP deployment;
- OAuth;
- Docker as a runtime requirement;
- FastMCP as a separate dependency layer;
- LangChain/LlamaIndex/CrewAI/AutoGen;
- Qdrant/Chroma/Milvus/pgvector;
- Redis/Postgres/Kafka/EventStoreDB;
- Polars/PyArrow unless the history implementation proves they are needed;
- `orjson`/`msgspec` without event-shape benchmarks;
- MCP Tasks;
- automatic multi-agent orchestration.

## 3. Security and trust model

The Agent Toolchain must not weaken the existing collector/privacy design.

### Files and databases

MCP tools never receive arbitrary filesystem paths or database paths. Clients use typed references only.

Read-side SQLite connections should be opened read-only when the service does not own writes. SQL is parameterized. There is no generic `execute_sql` MCP tool.

Raw HAR, cookies/auth, browser profiles, `.env`, operational SQLite/Parquet and raw session data remain outside Git.

### Untrusted content

Wiki/page/evidence text originates from an external site and must be treated as **untrusted data**, even when stored locally. Tool results should preserve provenance/trust metadata and never present observed page text as instructions for the agent.

High-level tools should prefer structural summaries and evidence refs over embedding full site text in `structuredContent`.

### Read/write separation

Future write automation is a separate process/entry point/server with a separate command allowlist and explicit enablement. The read MCP server must never import or expose write executors merely because a profile hides their tools.

## 4. Long-term package architecture

After PR #4, target structure:

```text
pyproject.toml
uv.lock

src/
  bizman/
    foundation/
    collector/
    changes/
    knowledge/
    sessions/
    index/
    projection/
    state/
    analytics/
    experiments/
    agent/
    cli.py

  bizman_mcp/
    server.py
    profiles.py
    resources.py
    tools/
      evidence.py
      sessions.py
      changes.py
      state.py
      analytics.py

tests/
evals/
tools/
  ci/
  benchmarks/
  migrations/
```

`tools/` becomes development/CI/migration infrastructure. Production implementations live under `src/`.

Migration must use compatibility imports/wrappers temporarily so existing commands/tests do not break in one giant rename.

## 5. Core API boundary

MCP must be an adapter over ordinary Python services.

Target logical interfaces:

```text
EvidenceStore
SessionStore
ChangeStore
IndexStore
StateStore
HistoryStore
AnalyticsService
ExperimentStore
ResolverService
```

Examples:

```text
core.evidence.trace(ref)
core.sessions.summary(session_ref)
core.changes.list(...)
core.resolve(query)
core.state.get(ref)
core.analytics.supply(ref)
```

The MCP layer only:

1. validates MCP input;
2. applies QueryBudget/profile permissions;
3. calls Core;
4. serializes the bounded structured result.

No business logic lives in MCP handlers.

## 6. Agent refs and retrieval

### Stable refs

Existing stable curated IDs such as `bm.action.units.vendor.select` remain canonical.

For entities without stable public IDs, define a versioned deterministic ref registry in the Agent Index. Do not invent model-generated IDs at query time.

Resolution flow:

```text
human query/name
      ↓
exact ref?
      ↓ no
alias / normalized exact lookup
      ↓ no
FTS5 ranked search
      ↓
canonical ref
```

Subsequent tools consume the canonical ref directly.

### Retrieval v1

Order:

1. canonical stable ref;
2. exact alias;
3. normalized structured fields;
4. SQLite FTS5/BM25;
5. prefix/trigram/fuzzy only if eval proves necessary.

No embeddings in v1.

FTS queries must go through a safe query builder; raw user MATCH/SQL syntax is not exposed directly.

### Retrieval acceptance gate

Create a deterministic retrieval corpus before MCP:

- exact ID queries;
- RU/EN aliases;
- action intent queries;
- endpoint/path queries;
- product/wiki queries;
- ambiguous names.

Measure Recall@1/5, MRR and evidence correctness. Embeddings are allowed only if lexical/structured retrieval fails the agreed threshold on real tasks.

## 7. Agent read model

`agent-index.sqlite3` is a derived read model, not a new source of truth.

Initial logical tables:

```text
ref
alias
knowledge_item
knowledge_evidence
knowledge_fts

session_summary
session_anomaly
change_index
index_meta
projection_checkpoint
```

Do not place full current-state economics tables here until the state projector exists.

Every index projection has:

```text
projection_name
projection_version
input_fingerprint
last_completed_at
```

The whole DB must be reproducibly rebuildable from curated Git knowledge + sanitized sessions + detector outputs.

## 8. Session intelligence

Session summarization should be deterministic derivation, not an LLM reading JSONL.

Minimum summary:

```text
event_count
action_count
http_request_count
http_response_count
strong/probable/temporal correlations
uncorrelated actions
new findings by kind
indeterminate evidence count
conflicts/warnings
```

`session.anomalies` should index unresolved correlations, failed/indeterminate interpretations, protocol changes and new findings.

Raw JSONL is an internal evidence source, not a normal agent retrieval surface.

## 9. MCP read-only v1

### SDK and protocol

Target official `modelcontextprotocol/python-sdk` 2.x with MCP `2026-07-28` support.

Initial transport: stdio only.

No Tasks dependency. The MCP Python SDK roadmap still lists the `io.modelcontextprotocol/tasks` extension as not implemented in the stable v2 line.

### Profiles

Initial profiles should be capability-oriented rather than one giant catalog.

`research`:

```text
evidence.resolve
evidence.search
evidence.get
evidence.trace
changes.list
changes.get
```

`logs`:

```text
sessions.list
sessions.summary
sessions.compare
sessions.anomalies
changes.list
changes.get
evidence.trace
```

`state` (only after State projector):

```text
state.get
state.query
state.diff
state.stale
evidence.get
```

`analytics` (only after analytics exists):

```text
analytics.supply
analytics.pricing
analytics.profitability
analytics.compare
state.get
```

`full` exists for diagnostics/evals, not as the default agent configuration.

### Standard result envelope

Use one bounded object shape where practical:

```json
{
  "summary": "...",
  "items": [],
  "refs": [],
  "evidence": [],
  "next_cursor": null,
  "truncated": false
}
```

Each MCP tool declares an output schema and returns `structuredContent` conforming to it.

### Query budgets

Enforced server-side:

```text
detail = compact | standard | full
limit
cursor
fields
```

Initial targets:

```text
compact default result: <= 8 KiB
standard hard target:   <= 16 KiB
items default:          <= 20
bounded evidence refs
bounded nesting depth
```

`full` remains bounded; it is not permission to stream an entire session.

### Resources

Large immutable objects can be exposed as resources, for example:

```text
bizman://evidence/<ref>
bizman://wiki/<ref>
bizman://session/<session-ref>/manifest
bizman://schema/event
```

Keep `evidence.get` as a bounded fallback because MCP host support for resources varies.

## 10. Current-state projector

The current-state DB is a materialized projection from immutable evidence, never authoritative input.

Target properties:

```text
projection_name
projection_version
input/evidence fingerprint
last session
last sequence
```

A parser/projector change must support:

```text
delete current.sqlite3
replay known evidence
=> deterministic equivalent current state
```

Initial domain priority:

1. companies/units;
2. products;
3. inventory/stock;
4. supply links/orders;
5. retail/prices;
6. production;
7. finance only when evidence is trustworthy enough.

A projection must refuse or mark stale when the Change Detector reports an incompatible/unknown structural change affecting its parser assumptions.

## 11. History and analytics

Storage roles remain separate:

```text
SQLite -> current operational/read state
Parquet -> immutable analytical history
DuckDB -> local analytical query engine
```

Do not introduce DuckDB before history/analytics work actually starts.

Initial deterministic metrics:

```text
stock days
consumption rate
supply gap
supplier capacity
price deltas
production throughput
profitability inputs
```

LLMs explain or compare deterministic calculations; they do not calculate core economic metrics from prose/raw events.

## 12. Experiment framework

The external proposal underemphasized the previously planned experiment layer. It remains necessary before autonomous write behavior because many game mechanics cannot be proven from passive observations alone.

Target flow:

```text
before-state snapshot
      ↓
explicitly authorized action / user action / BAS adapter
      ↓
network + DOM evidence
      ↓
after-state snapshot
      ↓
deterministic delta
      ↓
experiment result + evidence refs
```

Phase 1 experiments can correlate **user-executed** actions without BizMan generating writes itself.

Later controlled write experiments require the separate guarded executor and explicit approval.

Every experiment is replayable/auditable and distinguishes observed delta from hypothesis.

## 13. Model/agent routing

LLMs remain downstream of deterministic evidence.

Recommended eventual escalation:

```text
deterministic detector/index/state
        ↓
known/simple -> no LLM
        ↓ unknown promotion bundle
local Ornith 1.5 9B triage
        ↓ low confidence / complex
Luna high/xhigh
        ↓ architecture/mechanic ambiguity
Sol high
```

This routing is not implemented until a labelled evaluation set exists. Ornith/Luna/Sol are candidates to benchmark on the **same promotion/session tasks**, not hard-coded roles.

Specialist agents are also conditional:

- Log Analyst only for difficult session/change investigations;
- Evidence/Protocol Agent for evidence interpretation;
- Economics Agent only after State + deterministic analytics.

Common tasks should call Core/MCP tools directly.

## 14. Agent/retrieval benchmark

Create two distinct benchmarks.

### Deterministic retrieval benchmark

Tests the Agent Index without an LLM:

```text
Recall@1 / Recall@5
MRR
precision/evidence correctness
query latency p50/p95
bytes read
DB rows scanned where measurable
```

### Agent A/B/C benchmark

Use a frozen corpus snapshot and identical task set.

A — current repository/raw-file workflow.

B — deliberately naive MCP with many primitive tools.

C — proposed Agent Index + high-level MCP + profiles + budgets.

Task families:

- find endpoint/action/form;
- trace a write action;
- explain evidence for an endpoint;
- identify novelty;
- compare sessions;
- locate unresolved correlations;
- retrieve state after State projector exists;
- supply/profitability questions after analytics exists.

Record:

```text
task success
evidence correctness
input/model tokens
tool-schema tokens
tool-result tokens
tool calls/task
wall clock
p50/p95 tool latency
bytes returned
raw-file reads
```

Also record model/version/reasoning effort, prompt version, corpus fingerprint and active profile/tool-surface fingerprint. Run repeated trials for nondeterministic agent evaluations.

Initial acceptance targets for common read tasks:

```text
median tool calls <= 3
active tools/profile <= 8-10
default result <= 8 KiB
simple local query p95 <= 100 ms on reference CI/dev machine
session summary = 1 tool call
known action trace <= 1-2 calls
raw JSONL reads by agent = 0
evidence correctness >= baseline
```

Targets are gates to validate/refine, not marketing guarantees.

## 15. PR roadmap

### Completed

#### PR #1 — curated corpus/foundation

Structured knowledge, provenance, source identity, schemas and validation foundations.

#### PR #2 — passive CDP collector

Version-aware passive Chrome/CDP capture, sanitized immutable JSONL/CAS, real Chrome E2E and storage benchmark.

#### PR #3 — action context + correlation

Privacy-safe DOM action observation, isolated-world binding, deterministic action-to-HTTP correlation and security hardening.

### Current

#### PR #4 — deterministic Change Detector + Promotion Bundle

Keep current scope. Do **not** insert package/MCP migration into this PR.

Deliverables:

- semantic normalization/path matcher;
- compiled Runtime Contract IR;
- analysis profile versioning;
- streaming cryptographic evidence reader;
- Observation IR;
- SemanticDiff;
- stable versioned rules;
- `KNOWN/NOVEL/INDETERMINATE/CONFLICT` semantics;
- SQLite STRICT/WAL detector state;
- explicit `BEGIN IMMEDIATE` transaction control;
- transactional outbox;
- schema-valid value-free Promotion Bundle;
- synthetic integration/privacy tests and detector benchmarks.

Exit gate:

- all detector/foundation/collector tests green;
- full real-corpus baseline compile succeeds;
- corrupted evidence/CAS/path traversal tests fail closed;
- replay/idempotence/outbox crash cases covered;
- memory remains bounded on a large synthetic session;
- no sensitive values in bundles/state identity payloads;
- final PR review + CI green.

### Next

#### PR #5 — Python packaging + Core boundary

- add `pyproject.toml` and `uv.lock`;
- `requires-python >=3.11`;
- migrate production code from `tools/bizman_*` to `src/bizman/*`;
- compatibility shims for old imports/commands;
- introduce stable Core service/repository interfaces without MCP;
- add Ruff as dev-only dependency;
- switch CI installation to locked uv workflow;
- primary Python 3.14 validation + lightweight 3.11 compatibility lane;
- no Pydantic/MCP/DuckDB dependency unless required by code in this PR.

Exit gate: behavior-equivalent migration, no collector/detector regression, old entry points either work through shims or have documented replacement.

#### PR #6 — Agent Index + Session Intelligence

- `agent-index.sqlite3` read model;
- stable refs + alias registry;
- safe FTS5/BM25 retrieval;
- knowledge/evidence index;
- session summaries/anomalies;
- change index projection;
- `EvidenceStore`, `SessionStore`, `ChangeStore`, `ResolverService` concrete implementations;
- deterministic retrieval eval suite.

Exit gate: index fully rebuildable; no raw JSONL needed for common lookup tasks; lexical/structured retrieval meets acceptance target or produces evidence justifying a later embedding experiment.

#### PR #7 — read-only MCP v1

- official MCP Python SDK current tested 2.x;
- stdio transport only;
- `research` and `logs` profiles first;
- small bounded tool surface;
- strict input/output schemas and structured content;
- stable refs/progressive disclosure;
- large-object resources + bounded tool fallback;
- read-only annotations;
- stderr logging;
- no Tasks/HTTP/OAuth/write tools;
- MCP in-memory + stdio contract tests.

Exit gate: common evidence/session tasks require <=3 median calls in eval; no arbitrary paths/SQL; server stdout contains MCP protocol only.

#### PR #8 — replayable Current State Projector

- `current.sqlite3`;
- versioned projection checkpoints;
- company/unit/product/inventory/supply/price state;
- stale/incompatible projection handling tied to Change Detector findings;
- state diff/staleness services;
- add `state` MCP profile/tools after Core API is stable.

Exit gate: delete/replay produces equivalent current state from the same evidence/profile; projection refuses silently incompatible protocol changes.

#### PR #9 — Parquet history + DuckDB analytics

- history event/state snapshots to Parquet;
- DuckDB current stable 1.x dependency added only here;
- deterministic analytical views/services;
- supply/pricing/profitability metrics;
- `analytics` MCP profile.

No Polars/PyArrow unless benchmark/use case requires them.

Exit gate: analytical metrics are reproducible from history; MCP returns bounded metric results with evidence/state refs.

#### PR #10 — Experiment Framework

- experiment manifests;
- before/action/after evidence model;
- deterministic state deltas;
- user-action/BAS correlation adapter;
- experiment reproducibility and confidence semantics;
- no autonomous writes yet.

Exit gate: an experiment can prove/contradict a mechanic with explicit evidence refs and without an LLM deciding the delta.

#### PR #11 — Agent eval + retrieval/model optimization

- frozen 50-100+ task benchmark;
- A/B/C raw repo vs naive MCP vs optimized MCP;
- retrieval metrics;
- schema/result/token budget measurements;
- optional embedding experiment only if lexical retrieval misses targets;
- benchmark Ornith/Luna/Sol routing on labelled promotion/session cases.

Exit gate: optimized surface preserves/improves evidence correctness while reducing tool calls/context materially.

#### PR #12 — Recommendation layer

- deterministic recommendation inputs/metrics;
- strategy/ranking policy separated from observed state;
- uncertainty/staleness surfaced;
- model explanation layer optional;
- recommendations never become writes automatically.

#### PR #13+ — guarded write automation

Build only after State, experiments, recommendations and safety/eval layers are mature.

- separate write process/server;
- explicit allowlisted actions;
- preconditions and fresh-state checks;
- dry-run/planned request representation;
- explicit approval policy;
- idempotence where possible;
- post-action verification;
- audit trail;
- no cookies/auth leakage;
- read server remains incapable of writes.

## 16. CI strategy

The repository is public and currently has a PR quality gate. Keep CI focused rather than running every expensive integration on every unrelated change.

Target split after packaging:

```text
validate
  compile/lint/unit/schema/repo validation

detector-integration
  only detector/evidence/index relevant changes

collector-chrome-e2e
  collector/CDP/action/correlation relevant changes

benchmarks
  non-gating by default; explicit/PR when relevant

agent-evals
  non-gating initially; scheduled only if a future need justifies it
```

No routine `push` workflow and no schedule without a concrete monitoring requirement.

## 17. Dependency roadmap

### Base after PR #5

```text
Python >=3.11; 3.14 preferred
uv + pyproject.toml + uv.lock
websockets 17.x
jsonschema 4.x
Ruff dev-only
stdlib sqlite3
```

### Add at feature boundary

```text
MCP PR:       official mcp 2.x
Analytics PR: DuckDB 1.x
```

Pydantic is not a BizMan core requirement by default. If MCP contracts or another boundary clearly benefit from BizMan-owned Pydantic models, add it deliberately then.

## 18. External design references verified 2026-09-07

- MCP 2026-07-28 release: https://blog.modelcontextprotocol.io/posts/2026-07-28/
- MCP Python SDK v2 / PyPI: https://pypi.org/project/mcp/2.0.0/
- MCP Python SDK roadmap: https://github.com/modelcontextprotocol/python-sdk/blob/main/ROADMAP.md
- MCP tool annotations discussion: https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- uv dependency management: https://docs.astral.sh/uv/concepts/projects/dependencies/
- uv lock/sync: https://docs.astral.sh/uv/concepts/projects/sync/
- SQLite FTS5/BM25: https://www.sqlite.org/fts5.html
- DuckDB current stable installation/release: https://duckdb.org/install/
- Context7 resolve/query workflow: https://context7.com/docs/clients/cli
- Serena contexts/modes and reduced host-specific tool surfaces: https://oraios.github.io/serena/02-usage/050_configuration.html
- MotherDuck MCP bounded query outputs/security warning: https://github.com/motherduckdb/mcp-server-motherduck
- OpenAI Tool Search/deferred tools: https://openai.com/index/introducing-gpt-5-4/

## 19. North-star acceptance criteria

BizMan reaches the intended Agent Toolchain milestone when all of the following are true:

1. A user can play normally while the collector records sanitized immutable evidence.
2. New/changed protocol structure is detected deterministically and reviewably.
3. Common evidence/session questions never require an agent to read raw JSONL.
4. All current state is replayable from immutable evidence.
5. History and deterministic metrics are queryable without turning a DB into the source of truth.
6. MCP exposes a small bounded read-only surface with stable refs and evidence provenance.
7. Agent benchmarks show token/tool-call savings without lowering evidence correctness.
8. LLMs explain/triage evidence but do not manufacture observations or core metrics.
9. Experiments can verify uncertain mechanics before automation depends on them.
10. Any future write capability is physically separated, allowlisted, guarded, auditable and disabled by default.

The shortest path to this north star is therefore:

```text
PR4 Change Detector
  -> PR5 package/Core foundation
  -> PR6 Agent Index + Session Intelligence
  -> PR7 read-only MCP
  -> PR8 Current State
  -> PR9 History/Analytics
  -> PR10 Experiments
  -> PR11 Agent Evals/Optimization
  -> PR12 Recommendations
  -> PR13+ Guarded Writes
```
