# P2-E Agent Index / Core baseline — 2026-09-28

Source: PR #24, `collector-quality-gate` run 267, GitHub-hosted Ubuntu 24.04, Python 3.14.

This is the durable summary of the machine-readable `readmodel-benchmark` artifact. Timing is evidence, not a shared-runner gate.

## Corpus and workload

- curated knowledge records: 590;
- latency/runtime fixture: 20 sessions + 40 profile-scoped changes;
- larger rebuild fixture: 200 sessions + 400 changes;
- timing repetitions: 5;
- cold Core means a fresh validated `KnowledgeIndex` handle per call; OS page cache is not flushed;
- warm index means one already validated `KnowledgeIndex` handle is reused.

## Retrieval evaluation

| Corpus | Positive | Negative | Recall@1 | Recall@5 | MRR | Evidence | No-match |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 10 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | n/a |
| v2 | 8 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | n/a |
| v3 | 5 | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

v3 includes a real ambiguous title (`Универмаг`), natural-language lexical lookup, an FTS-required query, kind-filtered queries and explicit negative/no-match cases.

Decision: **keep the deterministic lexical stack** (canonical ref -> exact alias -> exact title -> FTS5/BM25). This run provides no evidence that fuzzy matching or embeddings are needed.

## Latency

Milliseconds:

| Operation | Cold Core p50 | Cold p95 | Warm index p50 | Warm p95 |
|---|---:|---:|---:|---:|
| resolve exact ref | 30.590 | 35.226 | 0.025 | 0.031 |
| resolve exact alias | 30.702 | 34.883 | 0.036 | 0.055 |
| FTS search | 30.291 | 30.828 | 0.114 | 0.131 |
| knowledge get | 30.123 | 30.523 | 0.107 | 0.116 |
| sessions page (20) | 30.304 | 30.392 | 0.273 | 0.283 |
| session get | 29.762 | 30.177 | 0.022 | 0.025 |
| changes page (20) | 30.482 | 30.650 | 0.195 | 0.210 |
| profile changes page (20) | 30.680 | 37.529 | 0.190 | 0.230 |
| change get | 30.128 | 30.321 | 0.019 | 0.022 |

Fresh index validation alone measured p50 **30.521 ms** / p95 **30.962 ms**.

Interpretation: cold Core latency is dominated by the intended open-time integrity verification, not by the SQL read itself. The measured cold p95 remains below 40 ms on this reference CI run and below the roadmap's later 100 ms simple-local-query target.

Decision: **do not weaken validation and do not add process-global caching in P2-E**. A future long-lived adapter may evaluate safe reuse of a validated handle only with explicit rebuild/generation lifecycle semantics.

## Rebuild and size

| Fixture | Sessions | Changes | Rebuild s | SQLite bytes | Peak tracemalloc bytes |
|---|---:|---:|---:|---:|---:|
| curated only | 0 | 0 | 0.104 | 1,490,944 | 2,774,563 |
| small runtime | 20 | 40 | 0.109 | 1,523,712 | 2,780,075 |
| larger runtime | 200 | 400 | 0.172 | 1,748,992 | 2,779,125 |

Each fixture produced a deterministic generation fingerprint and metadata counts matching the projected rows.

## Query-plan review

Observed plans:

- exact knowledge ref uses the `ref` primary index;
- exact session uses the `session_summary` primary index;
- exact and profile-filtered change reads use the composite `change_index` primary index;
- unfiltered change pagination scans the existing ordered primary index;
- FTS uses the FTS5 virtual table and indexed ref joins, with a temporary B-tree for score/ref ordering;
- session keyset pagination currently scans `session_summary` and uses a temporary B-tree for `started_at, session_id` ordering.

The session-page plan is not ideal asymptotically, but the measured warm p95 is 0.283 ms at the current latency fixture. P2-E therefore does **not** add a new SQLite index or bump schema identity without a larger-cardinality latency regression demonstrating material cost.

## Core result-size evidence

Representative UTF-8 canonical JSON sizes:

| Result | Bytes |
|---|---:|
| resolve exact ref | 174 |
| resolve exact alias | 399 |
| FTS search | 232 |
| knowledge get | 2,682 |
| session get | 593 |
| sessions page (20) | 11,670 |
| change get | 492 |
| changes page (20) | 10,150 |
| profile changes page (20) | 9,670 |

The 20-item session/change pages remain below the 16 KiB standard hard target but exceed the 8 KiB compact target.

Decision for P3 design: keep the Core hard limit of 50, but evaluate a smaller adapter default (for example 10 items) for compact MCP results rather than truncating Core results silently.

## P2-E outcome

- lexical retrieval: retained;
- fuzzy/embedding experiment: not justified by current evidence;
- integrity checks: retained unchanged;
- process-global read cache: not introduced;
- SQLite schema/index change: not justified at current measured scale;
- P3 output budgeting: needs a smaller compact adapter default for session/change lists;
- timing benchmarks remain non-gating on shared runners;
- correctness/evaluation remains gating.
