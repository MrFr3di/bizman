# BizMan unified roadmap

Status: stable delivery-stage roadmap, updated 2026-09-09.

BizMan evolves from deterministic evidence collection into a read-optimized agent platform and only later into guarded automation. Delivery stages use stable identifiers (`D1`, `P1`, `P2`, ...) rather than GitHub pull-request numbers. PR numbers are implementation history, not architecture.

## 1. North-star architecture

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

The central rule is non-negotiable: **immutable sanitized evidence is the source of truth; databases/indexes are rebuildable derivations; no LLM decides what was observed.**

## 2. Non-negotiable engineering invariants

1. Raw HAR, cookies/auth, browser profiles and operational state stay outside Git.
2. Collector, normalization, correlation, change detection, state projection and deterministic analytics do not depend on an LLM.
3. Unknown evidence is not treated as empty or known evidence.
4. Corrupted evidence, invalid paths, schema mismatches and incompatible state fail closed.
5. Every promoted change retains deterministic identity, reason, policy/version and provenance.
6. Derived SQLite/index/history stores remain rebuildable from immutable inputs.
7. Core services remain transport-neutral; CLI/MCP are adapters, not business-logic owners.
8. Read and future write capabilities remain physically separated.
9. Arbitrary filesystem paths and generic SQL are not public agent interfaces.
10. Write automation stays disabled by default until state, experiments, recommendations and safety/evals are mature.

## 3. Completed foundation stages

### F1 — Curated knowledge and provenance

Completed capabilities:

- structured corpus and authoritative catalog counts;
- normalized HTTP/routes/forms/actions/domain/Wiki datasets;
- stable source identities and aliases;
- JSON Schema validation and repository privacy guards;
- provenance/fingerprint primitives.

### F2 — Passive CDP Collector

Completed capabilities:

- version-aware Chrome/CDP discovery;
- passive command allowlist;
- first-party HTTP/WebSocket normalization;
- capture-time redaction;
- immutable JSONL + SHA-256 CAS storage;
- session lifecycle and UUIDv7 identities;
- real Chrome for Testing E2E;
- storage A/B/C benchmark.

### F3 — Action context and correlation

Completed capabilities:

- metadata-only DOM action observation;
- isolated-world binding/origin safety;
- no input/form values or page text collection;
- deterministic bounded action-to-HTTP correlation;
- explicit confidence semantics;
- lifecycle/replay/security hardening.

### D1 — Deterministic Change Detector + Promotion Bundle

Completed capabilities:

- semantic normalization and path matching;
- compiled Runtime Contract IR;
- replay-scoped analysis profile identity;
- cryptographically verified streaming evidence reader;
- value-free Observation IR;
- separated SemanticDiff and versioned rules;
- explicit `KNOWN / NOVEL / INDETERMINATE / CONFLICT` semantics;
- SQLite STRICT detector state, checkpoints and explicit `BEGIN IMMEDIATE`;
- transactional outbox and crash recovery;
- deterministic Draft 2020-12 Promotion Bundles;
- synthetic privacy/integration tests;
- large-stream detector benchmark.

D1 remains the correctness foundation for every downstream projection. Future parsers must not silently reinterpret incompatible evidence.

## 4. Current stage: P1 — Python package + Core boundary

Goal: turn the verified production implementation into an installable package and establish a small stable application boundary without changing Collector/Detector semantics.

Implemented in P1:

- `pyproject.toml` + exact `uv.lock`;
- Python `>=3.11`, Python 3.14 preferred/full validation;
- canonical `src/bizman` package;
- production `foundation`, `sessions`, `collector` and `changes` namespaces;
- thin compatibility re-exports/delegates for legacy `tools/bizman_*` imports/scripts;
- typed `RepositoryAssets` / `AssetId` boundary;
- injected timezone-aware UTC application clock;
- stable Core error hierarchy;
- immutable path-free Core request/result DTOs;
- Core collection/detection/validation use cases;
- unified `bizman collect|detect|validate` CLI;
- machine-enforced Import Linter dependency directions;
- Ruff package gate;
- isolated wheel/sdist build and clean-wheel installation proof;
- Python 3.11 compatibility lane plus Python 3.14 full lane.

P1 explicitly does **not** introduce MCP, Agent Index, Current State, DuckDB, recommendations or write automation.

P1 exit gate:

- package migration preserves semantic migration fingerprints;
- no `src/bizman -> tools.*` production dependency;
- Core API/export/error/clock/assets contracts are stable and tested;
- legacy supported entry points delegate to canonical package code;
- wheel/sdist contain no runtime/private/dev payloads;
- installed wheel imports and CLI work outside the checkout;
- Python 3.11 compatibility gate passes;
- Python 3.14 full validation passes;
- real Chrome E2E passes on the reviewed final head;
- no unresolved blocker review threads or security/runtime artifacts;
- branch is current with target branch before merge.

## 5. P2 — Agent Index + Session Intelligence

Goal: stop agents from scanning raw repository files/session JSONL for ordinary read tasks.

Primary store:

```text
BizManData/index/agent-index.sqlite3
```

The DB is a derived read model, not a source of truth.

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

### Stable refs and resolution

Existing curated IDs such as `bm.action.*` remain canonical. Other indexed objects receive deterministic versioned refs from the indexer, never model-generated IDs at query time.

Resolution order:

```text
canonical ref
  -> exact alias
  -> normalized structured fields
  -> SQLite FTS5/BM25
  -> optional fuzzy fallback only if eval proves needed
```

No embeddings in P2 by default.

### Deterministic session summaries

Minimum summary fields:

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

Session anomalies should index unresolved correlations, integrity/interpretation failures, protocol changes and detector findings.

### P2 acceptance gate

- index is fully rebuildable from curated knowledge + sanitized evidence + detector outputs;
- common lookup/session questions require zero raw JSONL reads by the agent;
- deterministic retrieval corpus measures Recall@1/5, MRR and evidence correctness;
- lexical/structured retrieval meets the agreed target or produces evidence for a later embedding experiment;
- projection metadata includes version, input fingerprint and completion time.

## 6. P3 — Read-only MCP v1

Goal: expose the P2/Core read surface through a small bounded MCP adapter.

Policy:

- official MCP Python SDK current tested `2.x` at implementation time;
- stdio first;
- no HTTP/OAuth/remote deployment in P3;
- no MCP Tasks dependency;
- handlers contain adapter logic only;
- stderr for logs; stdout reserved for protocol;
- read-only process cannot import/execute future write automation.

Initial capability profiles should remain small, for example:

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

Tool count is finalized by evaluation, not aesthetics. Default active profile target is roughly 8–10 tools or fewer.

### Result budgets

Initial targets:

```text
compact default result <= 8 KiB
standard hard target   <= 16 KiB
default items          <= 20
bounded evidence refs
bounded nesting depth
```

Large immutable objects can additionally be resources, while bounded tool fallbacks remain available for hosts with weaker resource support.

### P3 acceptance gate

- no arbitrary path/SQL tools;
- output schemas are explicit and bounded;
- common evidence/session tasks require <=3 median calls in evaluation;
- known action trace typically completes in <=1–2 calls;
- stdout is protocol-clean;
- evidence correctness is not lower than the pre-MCP baseline.

## 7. P4 — Replayable Current State

Goal: project trustworthy current game state from immutable evidence.

Store:

```text
BizManData/state/current.sqlite3
```

Target properties:

```text
projection_name
projection_version
input/evidence fingerprint
last session
last sequence
```

Initial domain priority:

1. companies/units;
2. products;
3. inventory/stock;
4. supply links/orders;
5. retail/prices;
6. production;
7. finance only when evidence is sufficiently trustworthy.

A projection must refuse or explicitly mark itself stale when D1 reports an incompatible/unknown structural change affecting parser assumptions.

Acceptance requirement:

```text
delete current.sqlite3
replay identical evidence/profile
=> deterministic equivalent current state
```

## 8. P5 — History + deterministic analytics

Storage roles remain separate:

```text
SQLite  -> current operational/read state
Parquet -> immutable analytical history
DuckDB  -> local analytical query engine
```

DuckDB is added only when P5 ships; it is not a base dependency.

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

LLMs may explain or compare these calculations but do not compute authoritative economics from prose/raw logs.

P5 acceptance gate: analytical outputs are reproducible from versioned history and retain state/evidence references.

## 9. P6 — Experiment framework

Goal: verify uncertain game mechanics before automation depends on them.

Target flow:

```text
before-state snapshot
      -> explicitly authorized user/BAS action
      -> network + DOM evidence
      -> after-state snapshot
      -> deterministic delta
      -> experiment result + evidence refs
```

Early experiments correlate user-executed actions; BizMan does not need to generate writes itself.

Each experiment records hypothesis, inputs, exact evidence/state refs, deterministic delta and confidence/outcome.

## 10. P7 — Retrieval/agent evaluation and model optimization

Two benchmarks are required.

### Deterministic retrieval benchmark

Measure:

```text
Recall@1 / Recall@5
MRR
precision/evidence correctness
query p50/p95
bytes read
rows scanned where measurable
```

### Agent A/B/C benchmark

A — repository/raw-file workflow.

B — deliberately naive MCP with many primitive tools.

C — Agent Index + high-level MCP + profiles + budgets.

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
model/version/reasoning effort
prompt/corpus/profile fingerprints
```

Initial common-path targets:

```text
median tool calls <= 3
default active tools <= 8-10
default result <= 8 KiB
simple local query p95 <= 100 ms on reference environment
session summary = 1 call
raw JSONL reads by agent = 0
evidence correctness >= baseline
```

Embeddings and model routing are allowed only when labelled evaluation demonstrates value.

Candidate model escalation can later be benchmarked on identical tasks, e.g. deterministic/no-LLM -> local model -> hosted model, but model names are not architectural dependencies.

## 11. P8 — Recommendation layer

Recommendations consume deterministic Current State/history/analytics rather than reconstructing facts from raw logs.

Requirements:

- recommendation inputs and metrics are explicit;
- observed state is separated from strategy/ranking policy;
- uncertainty and staleness are surfaced;
- model explanation is optional;
- recommendations never become automatic writes merely because they score highly.

## 12. P9+ — Guarded write automation

This is intentionally last.

Required properties:

- physically separate write process/server;
- explicit action allowlist;
- fresh-state and precondition checks;
- dry-run/planned-request representation;
- explicit approval policy;
- idempotence where possible;
- post-action verification;
- audit trail;
- no auth/cookie leakage;
- read server remains incapable of writes.

Write automation is introduced incrementally from proven protocol contracts and experiment evidence, not from guessed browser scripting.

## 13. Package/dependency policy

Base after P1:

```text
Python >=3.11; 3.14 preferred
uv + pyproject.toml + uv.lock
websockets 17.x
jsonschema 4.x
stdlib sqlite3
Ruff + Import Linter as dev dependencies
```

Add dependencies only at their feature boundary:

```text
P3 MCP:       official mcp 2.x
P5 analytics: DuckDB current reviewed 1.x line
```

Do not pre-add FastAPI, Pydantic, vector databases, Redis/Postgres/Kafka, LangChain/LlamaIndex/CrewAI/AutoGen, PyArrow/Polars or other infrastructure without a concrete workload/evidence-backed need.

`pyproject.toml` contains compatibility ranges; `uv.lock` is the exact reproducibility boundary.

## 14. Database separation

Do not collapse all derived state into one monolithic database by default.

Target operational layout:

```text
BizManData/
  detector/state.sqlite3
  index/agent-index.sqlite3
  state/current.sqlite3
  history/...
```

These stores have different projection/version/migration lifecycles. `bizman.core` aggregates them behind typed services. Consolidation requires evidence that cross-database cost is material enough to outweigh lifecycle isolation.

## 15. Security and trust model for agent layers

- external page/Wiki/evidence text is untrusted data, never instructions;
- tools prefer structural summaries + provenance refs over dumping page contents;
- read-side SQLite uses read-only connections where the service does not own writes;
- SQL is parameterized and not exposed as a generic public tool;
- clients operate on typed refs, not arbitrary filesystem/database paths;
- raw JSONL remains an internal evidence surface, not a normal agent interface;
- future write capability is a separate executable/process and permission domain.

## 16. CI evolution

Current P1 CI:

```text
validate (Python 3.14)
  lock + Ruff + Import Linter + compile + full tests + repository validation

compatibility (Python 3.11)
  compile + Core/migration/CLI contracts + public import/CLI smoke

collector-e2e
  real Chrome/CDP fixture after correctness lanes

benchmark
  storage evidence + non-gating detector performance
```

Later split by ownership when P2/P3 grow:

```text
package/core validation
collector Chrome E2E
detector integration
index/retrieval evals
MCP contract/stdio smoke
benchmarks (normally non-gating)
```

No routine push workflow and no schedule without a concrete monitoring need.

## 17. Stable delivery map

```text
F1  curated knowledge/provenance          completed
F2  passive CDP collector                 completed
F3  action context/correlation            completed
D1  deterministic Change Detector         completed
P1  Python package + Core boundary        current
P2  Agent Index + Session Intelligence    next
P3  read-only MCP v1
P4  replayable Current State
P5  Parquet history + analytics
P6  experiment framework
P7  retrieval/agent eval + optimization
P8  recommendation layer
P9+ guarded write automation
```

A GitHub PR or issue may implement one stage or a focused sub-slice, but stage identity never depends on that PR/issue number.

## 18. North-star acceptance criteria

BizMan reaches the intended agent-toolchain milestone when:

1. A user can play normally while sanitized immutable evidence is collected.
2. New/changed protocol structure is detected deterministically and reviewably.
3. Common evidence/session questions require no raw JSONL reads by an agent.
4. Current state is replayable from immutable evidence.
5. History and deterministic metrics are queryable without becoming source-of-truth inputs.
6. MCP exposes a small bounded read-only surface with stable refs/provenance.
7. Agent evaluations demonstrate tool/context savings without lower evidence correctness.
8. LLMs explain/triage evidence but do not manufacture observations or core metrics.
9. Experiments can verify uncertain mechanics before automation depends on them.
10. Any write capability is physically separated, allowlisted, guarded, auditable and disabled by default.

The shortest path from the current stage is therefore:

```text
P1 package/Core
  -> P2 Agent Index + Session Intelligence
  -> P3 read-only MCP
  -> P4 Current State
  -> P5 History/Analytics
  -> P6 Experiments
  -> P7 Agent Evals/Optimization
  -> P8 Recommendations
  -> P9+ Guarded Writes
```
