# BizMan unified roadmap

Status: stable delivery-stage roadmap, updated 2026-09-29.

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

## 4. Completed stage: P1 — Python package + Core boundary

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

## 5. Completed stage: P2 — Agent Index + Session Intelligence

Goal: stop agents and future adapters from scanning raw repository files/session JSONL for ordinary read tasks.

Primary store:

```text
BizManData/index/agent-index.sqlite3
```

The DB is a derived read model, not a source of truth. P2 is delivered as small vertical slices.

### P2-A — Knowledge Retrieval Kernel

Status: completed.

Initial scope is deliberately limited to curated objects that already have stable IDs and explicit provenance:

```text
11 actions
303 products
19 entities
= 333 indexed items
```

This slice establishes:

- canonical refs while preserving existing `bm.*` IDs;
- deterministic SQLite application/schema identity and atomic rebuild;
- explicit allowlisted projections instead of generic JSON flattening;
- exact ref -> exact alias -> exact title -> FTS5/BM25 resolution;
- bounded search results and bounded evidence refs;
- a versioned deterministic retrieval corpus measuring Recall@1/5, MRR and evidence correctness;
- no embeddings and no fuzzy matching unless later evaluation proves a need.

The `bizman.readmodel` package may consume deterministic lower layers but must not import collector, Core or CLI. Core integration is deferred until a useful read model exists.

### P2-B — Curated Corpus Coverage

Status: completed.

Extend explicit projectors to the remaining high-value curated corpora while preserving the P2-A retrieval contract.

Implemented scope:

```text
P2-A base               333
endpoints                68
operations               15
forms                    87
Wiki topics              87
---------------------------
total                    590
```

Identity/provenance policy:

- endpoints receive versioned deterministic refs from endpoint path identity;
- operations receive versioned deterministic refs from path + query-key + body-key identity, while existing `op-*` IDs remain aliases;
- forms preserve the existing deterministic `form_id` inside the versioned `bm.form.v1.*` namespace;
- Wiki topics receive versioned deterministic refs from topic identity;
- HAR observations are translated through the capture manifest to canonical `src.har.*#entry-N` evidence refs;
- session observations retain their sanitized session/sequence refs.

Searchable endpoint/operation/form text is allowlisted structure only: route, method, query-key names, field names/types and related structural metadata. Captured query/form/sample values are provenance, never FTS input. Wiki normalized documentation text remains searchable.

P2-B bumps the projection contract version while retaining SQLite schema v1. The P2-A retrieval corpus remains a regression suite; P2-B adds an extended corpus and must preserve Recall@1/5, MRR and evidence correctness.

### P2-C — Session + Change Intelligence

Status: completed.

P2-C projects sanitized runtime evidence and detector results into Agent Index without bypassing their owning boundaries:

- finalized sessions are read only through the public `EvidenceReader` API and are bound to verified manifest/evidence identity;
- detector changes are read only through the public `bizman.changes` summary boundary; `readmodel` does not query detector SQLite directly;
- `session_summary` stores session identity/time/status, event/action/HTTP counts, correlation buckets, uncorrelated actions and warning/anomaly counters;
- `change_index` stores profile-scoped change identity, rule/kind/novelty metadata, first/last session/time and occurrence count;
- change identity remains `(analysis_profile_sha256, change_id)`; different analysis profiles are never collapsed;
- Agent Index advances to SQLite schema/user_version 2 and projection version 3; pre-P2-C schema v1 is rejected and rebuilt rather than migrated in place;
- runtime rows and metadata are covered by deterministic fingerprints and fail closed on persisted invariant corruption.

This split keeps per-session evidence aggregates separate from detector change summaries. Higher-level grouping such as findings-by-kind belongs in the bounded P2-D read API rather than being duplicated into the storage schema without a concrete consumer.

### P2-D — Core Read API

Status: completed.

P2-D exposes the Agent Index through the stable application boundary without leaking SQLite, paths or `readmodel` implementation types:

- bounded Core operations for knowledge resolve/search/get, session list/get and profile-scoped change list/get;
- frozen/slotted, path-free Core-owned request/result DTOs;
- default list/search limit 20 and hard maximum 50;
- Agent Index location derived internally from `CoreContext.data_dir`;
- stable Core error translation for missing, incompatible, corrupt or operationally unreadable indexes;
- exact and bounded keyset query primitives in `KnowledgeIndex` rather than full-table loading in Core;
- opaque canonical/versioned cursors bound to operation/scope and semantic Agent Index generation, so pagination cannot silently continue across a changed rebuild;
- CLI and future MCP remain forbidden from importing `readmodel` directly.

P2-D also hardened persisted-index trust without changing SQLite schema v2 / projection v3: opening an index verifies the persisted curated-knowledge source fingerprint, FTS projection, foreign keys and runtime fingerprint. Knowledge integrity reconstruction is bulk-loaded to avoid an N+1 validation path.

### P2-E — Evaluation and hardening

Status: completed. Tracking issue: #22. Durable baseline: `docs/benchmarks/p2e-readmodel-baseline-2026-09-28.md`.

P2-E measures and hardens the completed P2 surface before MCP:

```text
Recall@1
Recall@5
MRR
evidence correctness
cold Core query p50/p95
warm KnowledgeIndex query p50/p95
rebuild time
integrity-validation cost
database size
serialized result size
```

The existing v1/v2 retrieval corpora remain regression baselines. A v3 corpus adds ambiguous, natural-language lexical, negative/no-match, kind-filtered and FTS-required cases. Query plans and open-time integrity cost are measured explicitly; timing stays non-gating on shared runners until evidence supports a stable threshold.

Resolution policy remains:

```text
canonical ref
  -> exact alias
  -> exact structured/title match
  -> SQLite FTS5/BM25
  -> optional fuzzy/embedding experiment only if eval proves needed
```

The v3 evaluation retained Recall@1/5, MRR, evidence correctness and no-match accuracy at 1.0 on the measured baseline, so lexical retrieval remains the production stack. Fuzzy/embedding retrieval is not justified by current evidence. Cold Core reads are dominated by deliberate integrity validation while warm index reads remain sub-millisecond; P2-E therefore preserves integrity checks and avoids a premature process-global cache or schema/index bump.

### P2 acceptance gate

- index is fully rebuildable from curated knowledge + sanitized evidence + detector outputs;
- identical semantic inputs produce the same generation fingerprint;
- common knowledge/session/change questions are answerable through bounded Core reads with zero raw repository/JSONL or direct SQLite scans by the agent;
- deterministic retrieval corpus reports Recall@1/5, MRR and evidence correctness;
- lexical/structured retrieval meets the agreed target or produces evidence for a later embedding experiment;
- projection metadata includes schema/projection version, source fingerprint and completion time;
- no arbitrary SQL/path read API is introduced.

## 6. P3 — Read-only MCP v1 (complete)

Goal: expose the P2/Core read surface through a small bounded MCP adapter.

Delivered by P3-A through P3-E:

- official MCP Python SDK 2.x; measured completion run used 2.2.0;
- local stdio transport only;
- thin `bizman.mcp` adapter consuming Core and no lower implementation layers;
- installed `bizman-mcp` console entry point;
- exactly 10 read-only, closed-world tools:

```text
evidence.resolve
evidence.search
evidence.get
evidence.trace
sessions.list
sessions.summary
sessions.compare
sessions.anomalies
changes.list
changes.get
```

Result policy:

```text
compact default result <= 8 KiB
standard ordinary result <= 16 KiB
MCP list default = 10
Core hard limit = 50
bounded evidence refs / aliases / result arrays
no silent truncation
```

P3-E replayed the P2-E v1/v2/v3 retrieval corpora through the actual MCP protocol surface and retained 1.0 for every applicable Recall@1/5, MRR, evidence-correctness and no-match metric. All 11 current action records resolved and traced in at most two MCP calls. The measured median for common evidence/session tasks was 1.0 call.

Measured P3-E result sizes:

- compact/default maximum: 6,230 bytes;
- representative ordinary maximum: 11,670 bytes.

The installed stdio process passed initialize, tools/list, tool call and close through the official SDK without stdout protocol corruption. Invalid UUID/limit/cursor/profile/evidence refs and a missing Agent Index all returned sanitized errors without traceback, SQLite detail or filesystem-path leakage.

Durable evidence: `docs/benchmarks/p3e-mcp-baseline-2026-09-29.md`.

### P3 acceptance gate — passed

- no arbitrary path/SQL tools;
- output schemas are explicit and bounded;
- common evidence/session tasks require <=3 median calls;
- known action trace completes in <=2 calls;
- stdout is protocol-clean;
- evidence correctness is not lower than the pre-MCP baseline;
- compact/default and ordinary result budgets are satisfied.

Frozen P3 decisions:

- keep the 10-tool default surface;
- keep stdio as the only P3 transport;
- do not add capability-profile switching without measured tool-discovery evidence;
- do not add resources, Streamable HTTP/OAuth, MCP Tasks, sampling or elicitation in P3;
- do not add fuzzy/embedding retrieval based on current evidence;
- MCP remains read-only and is not a state engine or write-automation layer.

## 7. Current stage: P4 — Replayable Current State

Goal: project trustworthy current game state from immutable evidence.

Store:

```text
BizManData/state/current.sqlite3
```

### P4-A — storage, identity and replay foundation (complete)

P4-A establishes the rebuildable Current State substrate before domain-specific state is introduced.

Delivered:

- independent `bizman.current` package and SQLite application/schema identity;
- STRICT/WAL store with fail-closed compatibility and integrity checks;
- deterministic projection metadata:
  - `projection_name`;
  - `projection_version`;
  - `analysis_profile_sha256`;
  - `input_fingerprint`;
  - `state_fingerprint`;
  - `ready/stale` status;
  - session count and final session/sequence checkpoint;
- immutable replay ledger over finalized EvidenceReader sessions;
- deterministic ordering by `(started_at, session_id)`;
- canonical UTC timestamps;
- shared analysis-profile compiler used by Change Detector and Current State;
- atomic full-snapshot replacement under `BEGIN IMMEDIATE`;
- fingerprint verification on persisted reads;
- path-free Core `rebuild_current_state` use case;
- Python 3.11/3.14, distribution and architecture contracts.

Acceptance proof:

```text
build current.sqlite3
snapshot A
delete current.sqlite3
replay identical immutable evidence/profile
snapshot B

A == B
A.state_fingerprint == B.state_fingerprint
```

P4-A intentionally contains no company/unit/product/inventory/price/production state tables and adds no CLI/MCP Current State surface.

### P4-B — companies / units projection (complete)

P4-B is the first end-to-end domain projection. The existing passive collector does not persist generic response bodies, so the slice first adds a deliberately narrow evidence capability for authoritative company roster pages rather than inferring current state from request intent or historical curated pages.

Target path:

```text
GET /company/?id=<company>&tab=units...
  -> bounded Network.getResponseBody
  -> capture-time sanitized text/title artifact in SHA-256 CAS
  -> immutable http.response_body event
  -> verified EvidenceReader replay
  -> deterministic company/unit parser
  -> latest-positive-observation reducer
  -> current.sqlite3 schema v2
```

P4-B rules:

- response-body capture is allowlisted to the company roster surface; generic HTML capture remains out of scope;
- scripts, styles, form values and HTML attributes are not persisted in the page artifact;
- historical `knowledge/pages/*` remains parser-regression evidence, never Current State input;
- company/unit identity is derived from one authoritative roster observation, never joined by display name;
- a successful write request does not mutate resulting state without a subsequent authoritative read;
- partial/paginated omission is UNKNOWN and never interpreted as deletion;
- known roster structure incompatible with parser v1 marks the projection `stale`;
- company/unit rows and provenance participate in the full state fingerprint;
- Current State advances to SQLite schema/user version 2 and projection version 2;
- an explicit rebuild recognizes the exact P4-A schema v1 contract, stages a fully verified v2 sibling database and atomically replaces the old derived store; a failed swap leaves v1 intact, while foreign/unidentified/newer databases remain fail-closed.

P4-B exit proof extends P4-A:

```text
collect synthetic authoritative company roster
replay -> company/unit Current State
snapshot A
delete current.sqlite3
replay identical immutable evidence
snapshot B

A == B
A.state_fingerprint == B.state_fingerprint
```

### P4-C — verified product identity and unit economics (current completion)

C0 passed on 2026-10-03. The authorized product identity is the numeric
`product=N` carried by two agreeing row-bound links in `table#goods` on
`GET /units/shop/?id=<unit>&tab=goods`. Display names and row order never
become identity.

Implemented:

- capture is allowlisted to the exact successful shop/goods read surface and
  produces canonical `bizman.unit-economics.v1` bytes rather than raw HTML;
- unit-economics rows contain bounded typed numeric values and stable product
  identity; unrelated labels, attributes, script text and arbitrary hidden
  fields are not persisted;
- replay projects `ObservedProduct` plus `UnitProductState` keyed by
  `(unit_id, product_numeric_id)`, with source session/sequence/time;
- curated numeric-id resolution is separate, nullable and fingerprinted by its
  normalization-relevant semantics;
- Current State projection/schema/user version is 3, with STRICT
  `observed_product`, `unit_product` and `product_surface_state` tables;
- every verified unit has explicit `shop.goods` coverage:
  `ready`, `unknown` or `stale`; missing goods evidence is UNKNOWN and is
  never interpreted as an empty roster or deletion;
- exact v1/v2 databases are rebuilt through a verified sibling v3 database and
  atomically replaced; foreign/newer/unknown schemas remain fail-closed;
- Core/CLI/MCP expose bounded read-only current-product reads; no write
  semantics are introduced.

C3 acceptance/hardening is the active slice. The real Chrome fixture exercises
the same typed goods capture/parser path with two stable product IDs and seeded
privacy canaries, then proves unit/product provenance and delete/replay
fingerprint equivalence. Orphan goods evidence for a unit that never appears in
the verified company roster remains immutable replay evidence and cannot become
an FK-backed authoritative association; its final derived representation is
still subject to the P4-C completion review.

Remaining domain priority after P4-C:

1. inventory/stock;
2. supply links/orders;
3. retail/prices;
4. production;
5. finance only when evidence is sufficiently trustworthy.

A projection must refuse or explicitly mark itself stale when D1 reports an incompatible/unknown structural change affecting parser assumptions. P4-A provides the explicit stale-state storage contract; automatic D1 binding remains a later P4 slice.

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

Current CI:

```text
validate (Python 3.14)
  lock + Ruff + Import Linter + compile + full tests with coverage + repository validation

compatibility (Python 3.11)
  compile + Core/migration/CLI contracts + public import/CLI smoke

collector-e2e
  real Chrome/CDP fixture after correctness lanes

benchmark
  storage evidence + non-gating Agent Index/Core and detector performance
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
P1  Python package + Core boundary        completed
P2  Agent Index + Session Intelligence    completed
P3  read-only MCP v1                      completed
P4  replayable Current State              current
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
P4 Current State
  -> P5 History/Analytics
  -> P6 Experiments
  -> P7 Agent Evals/Optimization
  -> P8 Recommendations
  -> P9+ Guarded Writes
```