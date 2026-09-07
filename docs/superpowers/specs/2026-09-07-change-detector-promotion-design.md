# Change Detector + Promotion Bundle Design

Status: approved architectural direction in chat on 2026-09-07.

## Goal

Turn sanitized local collector sessions into deterministic, reviewable novelty reports without committing operational data or putting an LLM in the evidence path.

The v1 detector answers a narrow question: **what protocol/form/action structure was observed at runtime that is not represented by the current curated corpus?**

It does not attempt to infer game economics, scrape response bodies, mutate curated knowledge automatically, or decide whether an observed change is strategically important.

## Data flow

```text
BizManData/sessions + events + sanitized CAS
                 |
                 v
            SessionReader
                 |
                 v
         SemanticExtractor
                 |
       +---------+----------+
       |                    |
       v                    v
 CuratedBaseline        DetectorState
 (repo, read-only)      (local SQLite)
       |                    |
       +---------+----------+
                 v
          ChangeDetector
                 |
                 v
         PromotionBundle
       (local, metadata-only)
                 |
                 v
          human/model triage
                 |
                 v
        curated knowledge/Git
```

Git remains code, schemas, documentation, and curated derived knowledge only. `BizManData`, detector SQLite files, local bundles, CAS, browser profiles, cookies, and auth state remain outside the repository.

## v1 novelty classes

The first detector version intentionally covers only structures that the current collector can observe safely and reliably.

### 1. Endpoint novelty

Source: `http.request` and `http.response` events.

Baseline: `knowledge/http/endpoints/index.json` and its partitions.

Detect:

- `endpoint.new`: request method/path does not match a known endpoint pattern;
- `endpoint.method_added`: path pattern is known but method is new;
- `endpoint.query_key_added`: method/path is known but a normalized query-key class is new;
- `endpoint.status_added`: response status for a known method/path is not represented by the baseline.

A baseline `{placeholder}` occupies exactly one path segment. Matching is segment-aware; it never uses an unconstrained substring wildcard.

Digit-only query-key names are canonicalized to `{numeric-key}` in both baseline and runtime observations. This prevents `/user/check/message?123=` style identifiers from generating a new change for every numeric key.

### 2. Form signature novelty

Source: `dom.action` events with form metadata, primarily `submit`.

Baseline: `knowledge/http/forms/index.json` and its partitions.

Canonical form key:

```text
(method, normalized action pathname)
```

Field names are normalized (`product[0]` -> `product[n]`) and redacted with the repository redaction policy before comparison. Baseline field values are never loaded into detector state or promotion bundles.

Detect:

- `form.new`: no form exists for the canonical method/action;
- `form.field_added`: the runtime safe field set contains a field class absent from every matching baseline form.

A runtime field set that is a subset of a known safe baseline form is not novel. This is required because sensitive baseline field names such as password fields are intentionally absent from runtime action events.

### 3. POST operation novelty

Source: first-party `http.request` events with method `POST`.

Baseline: `knowledge/http/operation-index.json`.

Canonical operation signature:

```text
(path pattern, normalized query-key set, normalized sanitized body-key set)
```

The detector may read only sanitized request-body CAS artifacts already produced by the collector. It extracts key names and discards values immediately. JSON bodies are considered only when they are objects or lists of objects; form bodies use `application/x-www-form-urlencoded`. Indexed names are canonicalized (`selected[397]` -> `selected[n]`).

Detect:

- `operation.new_signature`: no baseline operation for the matched path contains the same normalized query/body key sets;
- `operation.query_key_added` / `operation.body_key_added`: emitted when a closest same-path operation exists and the delta is additive.

No request-body values appear in a bundle.

### 4. Action-to-HTTP relation novelty

Source: `correlation.action_http` events with `strong` or `probable` status plus their referenced immutable action/request events.

Baseline: `knowledge/actions/catalog.json` plus operation signatures.

Detect:

- `action_http.new_relation`: a trusted/observed form action is strongly/probably linked to a request method/path that is not represented by the known action catalog, or its form action pathname conflicts with the linked request pathname.

`temporal-only` correlations never create an action relation promotion candidate in v1.

### 5. Structural conflicts

If a runtime observation is internally contradictory (for example a strong submit correlation where the safe form action path and HTTP request path disagree), the detector emits a `conflict` novelty class rather than silently choosing one interpretation.

## Normalization

Normalization is deterministic and value-free.

### Paths

- input must be an origin-relative absolute pathname beginning with `/`;
- query and fragments are not part of the path key;
- known endpoint placeholders match one non-empty slash-delimited segment;
- trailing-slash differences remain significant unless both forms are explicitly represented by the baseline. The detector does not invent server routing equivalence.

### Field/query keys

- surrounding whitespace is stripped;
- empty names are discarded;
- sensitive names are dropped with `RedactionPolicy.should_drop_field`;
- every `[digits]` component becomes `[n]`;
- a name consisting entirely of decimal digits becomes `{numeric-key}`;
- keys are deduplicated and lexicographically sorted before hashing/comparison.

### Status and methods

Methods are uppercase ASCII tokens. Status codes must be integers in 100..599; Python booleans are rejected as numeric values.

## Curated baseline

`CuratedBaseline.load(repo_root, redaction_policy)` reads only the declared curated files needed by v1:

- endpoint partitions from `knowledge/http/endpoints/index.json`;
- form partitions from `knowledge/http/forms/index.json`;
- `knowledge/http/operation-index.json`;
- `knowledge/actions/catalog.json`.

The loader ignores example values, form values, HAR entry payloads, and other unnecessary evidence data.

The baseline identifier is a SHA-256 hash over a canonical normalized semantic object built from endpoint/form/operation/action signatures. It is **not** a hash of file bytes. Reformatting JSON or repartitioning equivalent records therefore does not invalidate detector state.

Malformed baseline records fail closed with a path-specific error; the detector never silently treats an unreadable curated dataset as empty.

## Session input contract

`SessionReader` scans finalized local manifests under `BizManData/sessions/<session_id>/manifest.json`.

Accepted terminal statuses: `completed` and `cancelled`. A normally interrupted interactive collector session is still valid evidence. `failed` sessions are skipped by default and reported in the CLI summary.

Before detection, a session reader verifies:

- manifest is valid JSON and contains the expected session ID;
- all referenced event files exist under the configured data directory;
- every JSONL line is an object;
- every event has the same session ID;
- event `sequence` values are contiguous starting at zero;
- duplicate event IDs within a session are rejected.

Artifact references are resolved only through the collector SHA-256 CAS layout. Paths from event content are never treated as filesystem paths.

The final manifest fingerprint is SHA-256 over canonical JSON and is recorded in detector state. If the same `(session_id, baseline_sha256)` was already processed with a different manifest hash, the detector raises an immutability error rather than silently replacing history.

## Change identity

Each change is represented by a value-free semantic payload. Its fingerprint is SHA-256 over canonical JSON.

Stable ID:

```text
chg.<64 lowercase hex SHA-256>
```

The fingerprint excludes timestamps, session IDs, event IDs, counters, and display text. The same structural novelty therefore has the same change ID across sessions processed against the same baseline.

Evidence references are separate from identity and contain only sanitized event IDs/session ID plus normalized structural fields.

## Detector state

Default location:

```text
<BizManData>/detector/state.sqlite3
```

The database is operational and rebuildable; it is forbidden from Git.

Python uses the stdlib `sqlite3` module. On initialization the detector requires SQLite >= 3.37 and creates `STRICT` tables. Connections configure:

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

WAL is appropriate because detector state may later be inspected by a concurrent local reader while one detector writer is processing sessions. The database remains rebuildable from sanitized session history, so `synchronous=NORMAL` is an intentional durability/performance trade-off.

Schema v1:

```sql
CREATE TABLE detector_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE processed_sessions (
    session_id TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    bundle_id TEXT,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (session_id, baseline_sha256)
) STRICT;

CREATE TABLE seen_changes (
    baseline_sha256 TEXT NOT NULL,
    change_fingerprint TEXT NOT NULL,
    first_session_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_session_id TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
    PRIMARY KEY (baseline_sha256, change_fingerprint)
) STRICT;
```

State schema version is stored in `detector_meta`. Unknown newer schema versions fail closed; no destructive automatic migration is attempted.

A baseline change naturally creates a new namespace because both processed sessions and seen changes include `baseline_sha256`. Historical sessions can therefore be replayed against a new curated baseline without deleting the database.

## Idempotence and transaction ordering

Detection for one session is computed entirely in memory before state mutation.

Only **first-seen** change fingerprints for the active baseline are included in a new Promotion Bundle. Repeated observations update `occurrence_count` but do not emit duplicate bundles.

Commit ordering:

1. read/validate session and calculate all changes;
2. determine which change fingerprints are new using a read transaction;
3. construct a deterministic bundle using session terminal time and sorted changes;
4. atomically write the bundle using temp file + flush + `fsync` + `os.replace`;
5. enter `BEGIN IMMEDIATE`;
6. re-check processed-session and change uniqueness under the write lock;
7. upsert change counts and insert the processed-session checkpoint;
8. commit.

If the process crashes after step 4 but before SQLite commit, rerunning the session reproduces the same bundle ID/path and safely replaces the same deterministic content. If the transaction commits, the session checkpoint prevents a second emission.

## Promotion Bundle

Default local location:

```text
<BizManData>/promotions/<baseline-prefix>/<session_id>/<bundle_id>.json
```

A bundle is deterministic except for no wall-clock generation timestamp; it uses the finalized session's `ended_at` as `created_at`.

Bundle shape:

```json
{
  "schema_version": "1.0",
  "bundle_id": "promotion.<sha256>",
  "baseline_sha256": "...",
  "session_id": "...",
  "session_manifest_sha256": "...",
  "created_at": "...",
  "changes": [
    {
      "change_id": "chg.<sha256>",
      "kind": "endpoint.query_key_added",
      "novelty_class": "extended",
      "confidence": "observed",
      "subject": {"method": "GET", "path_pattern": "/example/"},
      "delta": {"added_query_keys": ["mode"]},
      "evidence_event_ids": ["..."]
    }
  ]
}
```

Allowed `novelty_class` values: `new`, `extended`, `conflict`.

Bundles contain no cookies, auth headers, query values, form values, request body values, raw HTML, WebSocket payloads, or arbitrary CAS bytes. They may contain structural key names that have already passed redaction.

A Draft 2020-12 schema in `schemas/promotion-bundle.schema.json` validates the persisted bundle.

## CLI

Entry point:

```text
python tools/detect_changes.py --data-dir ~/BizManData --repo-root .
```

Options in v1:

- `--session <uuid>` repeatable: process only selected sessions;
- `--dry-run`: detect and print summary without bundle or SQLite writes;
- `--redaction-policy <path>`: defaults to repository `config/redaction-policy.json`.

Default behavior scans finalized sessions in `(started_at, session_id)` order and skips already processed `(session_id, baseline_sha256)` checkpoints.

Output is a concise JSON summary containing baseline hash, scanned/processed/skipped session counts, first-seen change count, repeated change count, and written bundle paths.

A malformed session or baseline is a hard error. One corrupted evidence source must not be silently converted into “no changes”.

## LLM boundary

No LLM is used by `SessionReader`, `SemanticExtractor`, `ChangeDetector`, SQLite state, or bundle generation.

A later triage command may send **only the value-free Promotion Bundle** to an OpenAI-compatible provider. That future adapter can route cheap local work to Ornith and escalate ambiguous bundles to Luna/Sol, but model output is never accepted directly into curated knowledge without deterministic validation/review.

## Verification

Unit/contract tests must cover at least:

- placeholder endpoint matching and exact trailing-slash behavior;
- numeric query-key and indexed form/body-key normalization;
- known endpoint/form/operation suppression;
- every v1 novelty type;
- sensitive baseline form fields being ignored safely;
- method-only versus path-specific action relation handling;
- deterministic baseline/change/bundle fingerprints;
- no request/query/form values in bundles;
- corrupt/non-contiguous session rejection;
- CAS traversal resistance by reference-only resolution;
- same session/baseline idempotence;
- changed manifest rejection for an existing checkpoint;
- same session replay under a new baseline hash;
- SQLite STRICT schema, WAL mode, and transaction rollback;
- crash-safe deterministic bundle overwrite semantics;
- Draft 2020-12 validation of bundle fixtures.

PR-level integration should synthesize a small sanitized collector session rather than use real credentials or production game writes. Existing real Chrome collector E2E remains a regression gate because changes under `tools/**` continue to trigger it.

## Technology basis

- SQLite STRICT tables: https://www.sqlite.org/stricttables.html
- SQLite WAL: https://www.sqlite.org/wal.html
- Python `sqlite3`: https://docs.python.org/3/library/sqlite3.html
- JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12
