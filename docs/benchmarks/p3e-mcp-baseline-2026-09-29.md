# P3-E MCP completion baseline — 2026-09-29

Reference run:

- GitHub Actions workflow: `collector-quality-gate` run #295;
- head: `caecb0f01c845f999acd536aec5a65f49fb7f8aa`;
- runner: GitHub-hosted Ubuntu 24.04;
- Python: 3.14.7;
- MCP Python SDK: 2.2.0;
- curated knowledge records: 590;
- deterministic runtime fixture: 20 sessions + 40 changes.

The machine-readable authority for the run is the `mcp-p3-evaluation` workflow artifact. This document records the durable completion decision and measured values.

## Decision

**P3 — Read-only MCP v1 passes its completion gate.**

The default P3 server surface is frozen at exactly 10 read-only tools:

1. `evidence.resolve`
2. `evidence.search`
3. `evidence.get`
4. `evidence.trace`
5. `sessions.list`
6. `sessions.summary`
7. `sessions.compare`
8. `sessions.anomalies`
9. `changes.list`
10. `changes.get`

No additional default tools, capability-profile switching, MCP resources, Streamable HTTP/OAuth, Tasks, sampling, elicitation or writes are justified by the measured P3 evidence.

The only P3 transport remains local stdio.

## Retrieval parity

The P2-E retrieval fixtures were replayed through the actual MCP protocol surface using the official SDK.

| Corpus | Positive | Negative | Recall@1 | Recall@5 | MRR | Evidence | No-match |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 10 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | n/a |
| v2 | 8 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | n/a |
| v3 | 5 | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

Result: MCP retrieval does not regress the P2-E lexical baseline.

The deterministic lexical stack remains canonical:

```text
canonical ref
  -> exact alias
  -> exact title
  -> FTS5/BM25
```

P3-E provides no evidence for adding fuzzy or embedding retrieval.

## Known action provenance

All 11 current action knowledge records were exercised through:

```text
evidence.resolve
  -> evidence.trace
```

Measured result:

- actions resolved: 11 / 11;
- selected evidence refs traced: 11 / 11;
- maximum tool calls per action trace: 2;
- acceptance target: <=2.

This satisfies the roadmap requirement that a known action trace normally completes in one or two MCP calls.

## Common task call budget

Measured protocol calls:

| Task | Calls |
|---|---:|
| evidence.resolve | 1 |
| evidence.search -> evidence.get | 2 |
| evidence.resolve -> evidence.trace | 2 |
| sessions.list | 1 |
| sessions.summary | 1 |
| sessions.compare | 1 |
| sessions.anomalies | 1 |
| changes.list -> changes.get | 2 |

- median across all measured tasks: 1.0 call;
- median across evidence/session tasks: 1.0 call;
- acceptance target: <=3 median calls.

No evaluated task required raw repository JSON/JSONL, direct SQLite access or arbitrary filesystem paths.

## Protocol surface and schemas

Measured:

- tool count: 10;
- every tool advertises explicit `inputSchema` and `outputSchema`;
- every tool is annotated read-only and closed-world;
- forbidden path/SQL/database input properties: none;
- all output arrays are explicitly bounded;
- 20-item session/change pages returned the requested bounded page without silent truncation.

Published output collection bounds include:

- search/session/change/anomaly arrays: <=50;
- evidence refs: <=8;
- evidence aliases: <=64;
- missing comparison session IDs: <=2.

These are validation contracts, not truncation rules.

## Result-size evidence

Compact/default MCP structured-content sizes:

| Result | Bytes |
|---|---:|
| evidence.resolve | 174 |
| evidence.search | 180 |
| evidence.get | 238 |
| evidence.trace | 664 |
| sessions.list | 6,230 |
| sessions.summary | 593 |
| sessions.compare | 691 |
| sessions.anomalies | 2,532 |
| changes.list | 5,330 |
| changes.get | 492 |

Compact maximum: **6,230 B**, below the **8 KiB** target.

Representative 20-item results:

| Result | Bytes |
|---|---:|
| sessions.list(20) | 11,670 |
| sessions.anomalies(20) | 4,250 |
| changes.list(20) | 10,150 |

Standard maximum: **11,670 B**, below the **16 KiB** target.

This confirms the P2-E decision to keep Core's hard limit of 50 while using a smaller MCP default of 10 rather than silently truncating Core output.

## Error and privacy regression

Six representative failure classes passed the sanitized-error contract:

- invalid UUID;
- invalid limit;
- invalid cursor;
- invalid analysis profile;
- invalid evidence ref;
- missing Agent Index.

For each case the MCP result was an error without traceback, SQLite detail, repository path or operational data path leakage.

## stdio protocol cleanliness

The installed `bizman-mcp` console entry point was exercised through the official SDK:

- initialize: pass;
- tools/list: pass;
- tool call: pass;
- close: pass;
- advertised tools: exactly 10.

No stdout protocol corruption was observed. Diagnostics remain stderr-side.

## P3 completion outcome

All P3 acceptance conditions are satisfied:

- no arbitrary path/SQL tools;
- explicit bounded schemas;
- evidence/session median tool calls <=3;
- known action trace <=2 calls;
- protocol-clean installed stdio;
- retrieval/evidence correctness not below P2-E;
- compact/default result <=8 KiB;
- representative ordinary result <=16 KiB;
- deterministic sanitized error behavior.

### Frozen decisions

- keep exactly 10 default read-only MCP tools;
- keep stdio as the only P3 transport;
- do not add capability profiles without measured discovery/tool-selection evidence;
- do not add MCP resources merely because the protocol supports them;
- do not introduce HTTP/OAuth/Tasks/sampling/elicitation in P3;
- do not weaken Core/read-model integrity validation;
- do not add fuzzy/embedding retrieval based on current evidence.

## Next stage

P3 is complete.

The active delivery stage advances to **P4 — Replayable Current State**. P4 must continue to consume immutable evidence through deterministic Core/domain boundaries; it must not turn the MCP adapter into a state engine or write-automation layer.
