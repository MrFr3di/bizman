# Change Detector + Promotion Bundle Design

Status: revised production design after architecture audit on 2026-09-07.

This document supersedes the first 2D draft. The revised design was checked against the current BizMan collector/corpus, Python 3.14 `sqlite3`, SQLite STRICT/WAL/transaction guidance, JSON Schema Draft 2020-12, transactional-outbox guidance, and the normalization/diff/rule separation used by mature schema-diff tools such as Buf and oasdiff.

## Goal

Turn immutable, sanitized BizMan collector sessions into deterministic, replayable and reviewable structural findings without putting an LLM in the evidence path and without committing operational state.

The v1 question is deliberately narrow:

> What HTTP/form/action structure was observed at runtime that the current curated BizMan contract does not represent, and is there enough evidence to make that statement safely?

The last clause is essential. Missing evidence is never interpreted as an empty value or as “known”.

## Non-goals

2D v1 does not:

- mutate the game or browser;
- fetch response bodies that the collector did not persist;
- infer prices, economics or strategy;
- auto-edit curated knowledge;
- use an LLM to parse, normalize, compare, deduplicate or persist evidence;
- introduce Kafka, EventStoreDB, DuckDB or another server database;
- treat the current endpoint census as a complete protocol schema.

## Architectural principles

1. **Immutable evidence is the source of truth.** JSONL/CAS collector output is never rewritten by the detector.
2. **Comparison uses compiled semantic contracts, not source-file bytes.** Repartitioning or pretty-printing curated JSON must not create novelty.
3. **Interpretation is versioned.** Baseline, normalization/extraction semantics, redaction semantics and rule versions participate in an analysis profile.
4. **Unknown is first-class.** `None`/unavailable structural evidence is different from a confirmed empty set.
5. **Diff and policy are separate.** Semantic deltas are computed before versioned rules classify them as findings.
6. **Operational state is rebuildable.** SQLite is a local projection/checkpoint/outbox, not the evidence store.
7. **Promotion publication is crash-safe.** SQLite state and pending promotion payload are committed atomically; file materialization is idempotent downstream work.
8. **Everything promoted is value-free.** Only normalized paths, methods, status codes, structural key names, rule metadata and sanitized evidence IDs can leave the detector core.

## Target data flow

```text
curated knowledge
      |
      v
BaselineCompiler
      |
      v
RuntimeContract IR -------------------+
      |                                |
      |                          AnalysisProfile
      |                                |
      +--------------------------------+
                                       |
BizManData/session manifest            |
+ immutable JSONL                      |
+ sanitized CAS                        |
      |                                |
      v                                |
EvidenceReader + integrity checks      |
      |                                |
      v                                |
ObservationExtractor                   |
      |                                |
      v                                |
Observation IR                         |
      |                                |
      +---------------> SemanticDiff <-+
                              |
                              v
                           DiffFacts
                              |
                              v
                      versioned RuleEngine
                              |
                 +------------+------------+
                 |            |            |
               known      indeterminate   finding
                                           |
                                           v
                           SQLite STRICT/WAL transaction
                           changes + checkpoint + outbox
                                           |
                                           v
                           Promotion materializer
                                           |
                                           v
                              value-free bundle
                                           |
                                           v
                                human/model triage
                                           |
                                           v
                              curated knowledge/Git
```

## Package boundaries

```text
tools/bizman_detector/
├── __init__.py          public version constants only
├── model.py             immutable IR/value objects and enums
├── normalization.py     pure canonicalization + path matcher
├── baseline.py          curated sources -> RuntimeContract
├── evidence.py          manifests/JSONL/CAS validation + hashing
├── extract.py           validated events -> Observation IR
├── diff.py              observations vs contract -> DiffFacts
├── rules/
│   ├── __init__.py      rule registry + analysis-profile rule metadata
│   ├── http.py
│   ├── forms.py
│   ├── operations.py
│   └── relations.py
├── state.py             SQLite schema/checkpoints/changes/outbox
├── promotion.py         bundle builder/schema validation/materialization
└── runner.py            orchestration only
```

No plugin framework is introduced in v1. `rules/*` are plain deterministic Python functions/classes with stable IDs and integer versions.

## Version model

The detector has explicit semantic versions independent of package release numbers:

```text
CONTRACT_SCHEMA_VERSION = 1
NORMALIZATION_VERSION   = 1
EXTRACTION_VERSION      = 1
PROMOTION_SCHEMA_VERSION = 1
```

Each rule has `rule_id` and `rule_version`.

### Baseline identity

`baseline_sha256` hashes a canonical normalized `RuntimeContract` envelope:

```json
{
  "contract_schema_version": 1,
  "normalization_version": 1,
  "endpoints": [],
  "forms": [],
  "operations": [],
  "action_request_families": []
}
```

It does not hash partition bytes, record ordering, examples, counts or values.

### Redaction identity

The detector computes `redaction_policy_sha256` from semantic policy content: sorted dropped headers, field pattern text+flags, body limits, first-party flag and normalized MIME allowlist. This prevents an interpretation change from silently reusing old checkpoints even when current baseline records happen not to be affected by the changed policy.

### Analysis profile

`analysis_profile_sha256` is the checkpoint namespace:

```text
SHA256({
  baseline_sha256,
  contract_schema_version,
  normalization_version,
  extraction_version,
  redaction_policy_sha256,
  rules: sorted [{rule_id, rule_version}]
})
```

A baseline update, normalization change, extraction change, redaction-policy change or rule-version change therefore replays historical sessions automatically without deleting SQLite.

## Runtime Contract IR

The current curated corpus is intentionally heterogeneous. The compiler turns it into one value-free contract before runtime comparison.

### Endpoint families

Sources:

- endpoint path patterns from `knowledge/http/endpoints/index.json` partitions;
- concrete observations from `knowledge/http/application-events/index.json` partitions.

The endpoint census alone is insufficient because `methods`, `statuses` and `query_keys` are separately aggregated and lose the exact method/status relationship. Therefore each curated application event is matched to exactly one endpoint path pattern, and endpoint contract information is compiled per `(path_pattern, method)`.

For each method/path family retain only:

- normalized path pattern;
- method;
- union of observed safe query-key classes for that method/path;
- set of observed response status codes for that method/path.

Values, timestamps, capture names, response hashes and examples do not enter the contract identity.

If a curated application event matches no endpoint pattern or is ambiguous at the highest specificity, baseline compilation fails with a source-specific consistency error. It is not silently converted into a literal fallback.

### Path matching and ambiguity

Paths are origin-relative absolute pathnames. v1 does **not** percent-decode or Unicode-normalize them; doing so can change slash/segment semantics (for example `%2F`). Query and fragment components are not part of path identity.

A `{placeholder}` occupies exactly one non-empty slash-delimited segment. Literal segments are escaped, not interpreted as regex.

Matching priority:

1. exact literal path;
2. matching template with the greatest number of literal segments;
3. then the fewest placeholders.

If two distinct patterns remain tied at highest specificity, matching is ambiguous and the detector fails closed. JSON/source ordering never decides a match.

Trailing slash remains significant.

For performance, exact paths use a dictionary and templates are bucketed by segment count; a trie is unnecessary for the current 68-endpoint corpus.

### Methods

Methods are canonicalized to uppercase after ASCII-token validation. Whitespace-containing or non-token values are invalid evidence, not normalized guesses.

### Query/field key normalization

Normalization order:

1. input must be a string;
2. trim surrounding Unicode whitespace;
3. discard empty names;
4. apply `RedactionPolicy.should_drop_field` to the trimmed original name;
5. replace each ASCII `[0-9]+` index component with `[n]`;
6. if the entire name matches ASCII `[0-9]+`, replace with `{numeric-key}`;
7. deduplicate and lexicographically sort sets.

ASCII digits are intentional; `str.isdecimal()` is not used because it gives broader Unicode semantics that are harder to reproduce across producers.

### Forms

Source: `knowledge/http/forms/index.json` partitions.

A form has two identities:

```text
FormFamilyKey = (method, canonical action path)
FormSignature = sorted safe normalized field-name set
```

The action path uses endpoint pattern canonicalization when it uniquely matches an endpoint; otherwise a valid literal origin-relative path remains literal.

Sensitive field names and all values are discarded before IR construction.

At runtime:

- a field set that is a subset of a known signature is known (runtime intentionally omits sensitive fields);
- additional safe fields produce an additive form diff;
- a family with no compatible signature may produce `form.signature_new` rather than conflating family existence with signature identity.

### POST operations

Source: `knowledge/http/operation-index.json`.

Canonical signature:

```text
(canonical path pattern, normalized query-key set, normalized body-key set)
```

Multiple signatures for one path (notably `/units/vendor/select/`) are first-class and must not be collapsed.

For runtime evidence, `body_keys=None` means **structurally unknown/unavailable**. It is never equivalent to `body_keys=()`.

The current collector omits `request_body_ref` when a body is absent, over the capture limit, unsupported by MIME policy, malformed or structurally unsafe. Because those cases are not distinguishable from the event alone, absence of `request_body_ref` is `UNKNOWN_BODY` in v1. No operation novelty rule fires from unknown body evidence.

### Action/request families

Source: `knowledge/actions/catalog.json` plus operation contracts.

The curated action catalog proves known write request families `(method, path)` and structural form/query keys. It does not prove a full historical DOM-action graph.

Therefore v1 relation rules are deliberately narrow:

- a strong/probable correlation to an unseen write request family can be a new finding;
- a safe form action path that conflicts with its linked request path is a separate conflict finding;
- `temporal-only` correlation never creates a promotion finding.

## Evidence reader and integrity

Accepted session statuses are `completed` and `cancelled`. `failed` sessions are skipped by default and reported.

### Manifest

The reader validates manifests against `schemas/session-manifest.schema.json` with one reusable Draft 2020-12 validator and `FormatChecker`.

It verifies the path directory name equals `session_id`.

### Event files

Manifest event paths must:

- be relative POSIX-style paths;
- contain no `..`;
- resolve under the configured data directory even through symlinks;
- reside under `events/`;
- refer to regular `.jsonl` files.

The reader streams event files in manifest order. It never loads the whole JSONL session into memory.

Each binary line has a maximum size guard. Every line must be valid UTF-8 JSON, an object, pass `schemas/event.schema.json`, use the session ID, and have a contiguous sequence starting at zero. Duplicate event IDs are rejected.

A reusable Draft 2020-12 event validator is created once per reader, never per event.

### Evidence identity

`manifest_sha256` is SHA-256 over canonical manifest JSON.

Each event file is SHA-256 hashed over its actual bytes while streaming. The session evidence identity is:

```text
evidence_sha256 = SHA256({
  manifest_sha256,
  event_files: ordered [{path, sha256, bytes}]
})
```

Checkpoint immutability is based on `evidence_sha256`, not only the manifest hash. Editing a JSONL file without changing the manifest is therefore detected.

### CAS verification

Only references matching `sha256:<64 lowercase hex>` are accepted. Paths are derived from the digest; event content is never treated as a path.

Whenever an artifact is dereferenced, the reader recomputes SHA-256 over the actual bytes and requires it to equal the reference digest. Mismatch is corruption and aborts processing.

Body artifacts retain the collector size bound; the detector also enforces an independent maximum read size.

## Observation IR

Validated events are projected immediately to immutable value-free observations. Raw event dicts and headers are not retained after extraction.

Core v1 types:

```text
HttpObservation
  method
  literal_path
  canonical_path_pattern | None
  query_keys
  status | None
  body_keys: tuple[str,...] | None
  request_event_id
  response_event_id | None

FormObservation
  method
  action_path
  field_names
  action_event_id

RelationObservation
  correlation_status
  action_event_id
  request_event_id
  safe action/request structural metadata
```

For body keys:

```text
None  = evidence unavailable / indeterminate
()    = confirmed structurally empty body (reserved for evidence formats that can prove it)
```

With current collector output, no `request_body_ref` yields `None`.

The extractor may retain minimal action/request metadata needed to resolve later correlation references, but never whole raw events. Pending relation-source state has a hard upper bound; exceeding it fails closed rather than risking unbounded memory.

## Semantic diff

`diff.py` compares Observation IR with RuntimeContract and emits data-only `DiffFact` values. It does not know Promotion Bundle format or persistence.

Comparison states are:

```text
KNOWN
NOVEL
INDETERMINATE
CONFLICT
```

Examples:

- unknown body on a POST -> `INDETERMINATE`, not `operation.new_signature`;
- ambiguous path match -> hard baseline/matching error, not NOVEL;
- strong form/action path disagreement -> `CONFLICT`;
- extra safe query key on a known method/path -> NOVEL additive fact.

`INDETERMINATE` diagnostics are counted in run summaries but are not promoted as changes in v1.

## Rule catalog

Rules consume DiffFacts and produce immutable Findings. They have stable IDs and integer versions. v1 catalog:

| Rule ID | v | Finding kind |
|---|---:|---|
| `BM-HTTP-001` | 1 | `endpoint.new` |
| `BM-HTTP-002` | 1 | `endpoint.method_added` |
| `BM-HTTP-003` | 1 | `endpoint.query_key_added` |
| `BM-HTTP-004` | 1 | `endpoint.status_added` |
| `BM-FORM-001` | 1 | `form.signature_new` |
| `BM-FORM-002` | 1 | `form.field_added` |
| `BM-OP-001` | 1 | `operation.new_signature` |
| `BM-OP-002` | 1 | `operation.query_key_added` |
| `BM-OP-003` | 1 | `operation.body_key_added` |
| `BM-REL-001` | 1 | `action_http.request_family_new` |
| `BM-REL-002` | 1 | `action_http.path_conflict` |

A rule test asserts the exact set of rule IDs produced by a fixture, not merely that one expected finding is present.

### Finding identity

Finding identity excludes timestamps, session IDs, event IDs, occurrence counters and display prose. It includes semantic versioning:

```text
change_id = "chg." + SHA256({
  normalization_version,
  extraction_version,
  rule_id,
  rule_version,
  kind,
  novelty_class,
  subject,
  delta
})
```

Evidence IDs remain outside identity and are merged/sorted for duplicate same-session findings.

## Detector state

Default:

```text
<BizManData>/detector/state.sqlite3
```

The DB is operational/rebuildable and forbidden from Git.

Minimum SQLite: 3.37 for STRICT tables.

Every read-write connection configures immediately:

```sql
PRAGMA trusted_schema = OFF;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

WAL is used only on local filesystems. BizMan does not support detector state on a network filesystem.

`NORMAL` is intentional because the projection is rebuildable; SQLite documents WAL+NORMAL as consistent but potentially losing the last committed transaction on power loss, which replay can recover.

Database identification/versioning uses:

```text
PRAGMA application_id = 1112359985   -- 0x424D4431 = "BMD1"
PRAGMA user_version = 1
```

A nonzero foreign application ID or unknown newer user_version fails closed. No destructive automatic migration is performed in v1.

### Explicit Python transaction control

Python 3.14 recommends the `autocommit` API. BizMan needs explicit `BEGIN IMMEDIATE`, not implicit `BEGIN DEFERRED`.

On Python >=3.12 the state connection uses SQLite autocommit mode (`autocommit=True`) and executes SQL `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` explicitly. On Python 3.11 compatibility mode uses `isolation_level=None`, which likewise leaves transaction boundaries to explicit SQL.

This choice is deliberate: PEP-249 `autocommit=False` keeps a DEFERRED transaction open and conflicts with our explicit IMMEDIATE write-lock boundary.

### Schema v1

```sql
CREATE TABLE processed_sessions (
    session_id TEXT NOT NULL,
    analysis_profile_sha256 TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (session_id, analysis_profile_sha256)
) STRICT;

CREATE TABLE changes (
    analysis_profile_sha256 TEXT NOT NULL,
    change_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version INTEGER NOT NULL CHECK (rule_version > 0),
    kind TEXT NOT NULL,
    novelty_class TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    first_session_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_session_id TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
    PRIMARY KEY (analysis_profile_sha256, change_id)
) STRICT;

CREATE TABLE promotion_outbox (
    bundle_id TEXT PRIMARY KEY,
    analysis_profile_sha256 TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL,
    session_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','materialized')),
    created_at TEXT NOT NULL,
    materialized_at TEXT
) STRICT;
```

`identity_json` and `payload_json` contain only canonical value-free detector structures. Keeping them makes state auditable even if a materialized local file is removed.

## Transactional outbox and idempotence

The old “write bundle file, then commit SQLite” order is replaced because it is a filesystem/database dual write.

Per session:

1. validate/hash evidence and build Observation IR;
2. compute DiffFacts and Findings in memory;
3. execute `BEGIN IMMEDIATE`;
4. re-check `(session_id, analysis_profile_sha256)`;
5. determine first-seen findings under the write lock;
6. build deterministic canonical bundle payload for first-seen findings, if any;
7. upsert occurrence counts;
8. insert the processed-session checkpoint;
9. insert the pending outbox row containing the exact bundle payload;
10. `COMMIT`;
11. materialize pending outbox rows to files using atomic temp-write + file `fsync` + `os.replace` + parent-directory fsync where supported;
12. mark successfully verified files `materialized` in a short subsequent transaction.

No filesystem I/O occurs inside the main write transaction.

Crash cases:

- before commit: no state is visible;
- after commit but before file write: pending outbox survives and next run writes it;
- after file write but before mark: next run verifies identical bytes/hash and marks it;
- existing file with different bytes for the deterministic bundle path: hard integrity error.

This is the transactional-outbox pattern adapted to a local file materializer rather than a message broker.

## Promotion Bundle

Default path:

```text
<BizManData>/promotions/<analysis-profile-prefix>/<session_id>/<bundle_id>.json
```

Envelope includes provenance sufficient to replay interpretation:

```json
{
  "schema_version": "1.0",
  "bundle_id": "promotion.<sha256>",
  "analysis_profile_sha256": "...",
  "baseline_sha256": "...",
  "redaction_policy_sha256": "...",
  "session_id": "...",
  "evidence_sha256": "...",
  "manifest_sha256": "...",
  "created_at": "<session ended_at>",
  "detector": {
    "normalization_version": 1,
    "extraction_version": 1
  },
  "findings": [
    {
      "change_id": "chg.<sha256>",
      "rule_id": "BM-HTTP-003",
      "rule_version": 1,
      "kind": "endpoint.query_key_added",
      "novelty_class": "extended",
      "evidence_confidence": "observed",
      "subject": {"method": "GET", "path_pattern": "/example/"},
      "delta": {"added_query_keys": ["mode"]},
      "evidence_event_ids": ["..."]
    }
  ]
}
```

`bundle_id` is SHA-256 of the canonical payload excluding the `bundle_id` field itself.

`schemas/promotion-bundle.schema.json` uses Draft 2020-12 and `additionalProperties:false` for all stable envelopes.

Bundles never contain query values, body values, form values, headers, cookies, auth material, raw HTML, WebSocket payloads or arbitrary CAS bytes.

## Dry-run semantics

`--dry-run` performs full baseline/evidence validation, extraction, diff and rules without creating or modifying SQLite/bundles.

If an existing detector DB exists, dry-run may open it read-only to classify findings as already-seen versus first-seen. If no DB exists, it uses an empty state view. It never materializes pending outbox rows.

## LLM boundary

No model participates before Promotion Bundle materialization.

A later triage adapter can send only schema-valid value-free bundles to an OpenAI-compatible provider. Local Ornith can perform cheap first-pass classification; ambiguous bundles can escalate to Luna/Sol. Model output is advisory and cannot mutate curated knowledge without deterministic validation/review.

## Performance and resource bounds

- baseline compiles once per detector invocation;
- exact endpoint matching is O(1); templates are bucketed by segment count;
- JSONL input is streamed;
- event schema validator is reused;
- raw events are not accumulated;
- relation-source indexes contain only minimal metadata and have a hard bound;
- CAS artifacts have an independent detector read-size cap;
- SQLite writes are one IMMEDIATE transaction per processed session;
- outbox file writes occur after commit.

A non-gating benchmark will compare detector throughput/memory on a large synthetic session. Performance tuning must not disable schema validation, integrity hashing or CAS digest verification.

## CI and verification policy

The repository is public, so GitHub-hosted standard-runner billing is not the old private-repository constraint. CI is still batched by meaningful TDD/review checkpoints rather than used as a substitute for design.

Detector-only changes require:

```text
compileall
unit/contract/deep-regression tests
validate_repo.py
synthetic detector integration
promotion schema validation
idempotence + crash/outbox recovery tests
```

The real Chrome collector E2E remains a final regression gate before merge. It does not need to run as the development loop for every detector-only commit unless shared collector/foundation code is changed.

Security/integrity tests additionally scan persisted promotion/outbox/SQLite content for synthetic forbidden values.

## Required test classes

At minimum:

- ASCII numeric/index field normalization;
- exact/template path matching, specificity and ambiguity rejection;
- percent-encoded path preservation and trailing-slash exactness;
- baseline compilation from endpoint patterns + application-event method/status/query observations;
- multiple operation signatures for `/units/vendor/select/`;
- sensitive baseline fields removed;
- semantic baseline hash stable across ordering/repartitioning;
- analysis profile changes when baseline/redaction/rule/extraction semantics change;
- manifest Draft 2020-12 validation;
- event Draft 2020-12 validation with one reusable validator;
- oversized/non-UTF8/truncated JSONL rejection;
- event-file mutation changes evidence hash;
- CAS traversal rejection and digest mismatch rejection;
- `UNKNOWN_BODY` never becoming an empty operation signature;
- exact rule-ID sets for all v1 known/novel/indeterminate/conflict fixtures;
- stable change identity excluding evidence IDs/timestamps;
- SQLite STRICT/WAL/trusted-schema/application-id/user-version checks;
- explicit `BEGIN IMMEDIATE` rollback/contention behavior;
- replay under changed analysis profile;
- transactional outbox crash cases;
- deterministic promotion bytes/path;
- no synthetic secret value in bundle, outbox payload or SQLite file;
- end-to-end detector rerun idempotence;
- non-gating large-session throughput/memory benchmark.

## Technology and practice basis

Official/current references used by this design:

- Python 3.14 `sqlite3` transaction control: https://docs.python.org/3/library/sqlite3.html
- SQLite STRICT tables: https://www.sqlite.org/stricttables.html
- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite transactions / BEGIN IMMEDIATE: https://www.sqlite.org/lang_transaction.html
- SQLite PRAGMAs / trusted_schema / synchronous / user_version: https://www.sqlite.org/pragma.html
- SQLite database application ID: https://www.sqlite.org/fileformat.html
- JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12
- AWS transactional outbox: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
- AWS event sourcing: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/event-sourcing-pattern.html
- Buf breaking-change baseline model: https://buf.build/docs/breaking/
- oasdiff diff/check separation: https://github.com/oasdiff/oasdiff/blob/main/docs/DIFF.md
- oasdiff rule/check conventions: https://github.com/oasdiff/oasdiff/blob/main/docs/CUSTOMIZING-CHECKS.md
- OpenTelemetry local file-storage operational-state guidance: https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/extension/storage/filestorage/README.md
