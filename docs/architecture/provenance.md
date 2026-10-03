# Provenance model

BizMan keeps provenance as stable, bounded identifiers rather than committing raw browser captures or historical session streams.

## Evidence reference formats

Committed knowledge records currently use two evidence locator forms:

```text
HAR capture:
SOURCE_ID#entry-N

Promoted historical collector session:
SOURCE_ID#seq-N
```

Examples:

```text
src.har.bizmania.2026-09-06.01#entry-224
live-cdp-2026-09-07#seq-25730
```

HAR entry ordinals and collector sequence numbers are zero-based. A locator is valid only when it is inside the registered source record/event count.

## Canonical source registries

HAR capture truth is split across:

- `knowledge/sources/captures.json` — canonical source id, file name, SHA-256, size, entry count and capture interval;
- `knowledge/sources/*.har.json` — per-source manifest and privacy metadata;
- raw HAR bytes are intentionally not committed.

Promoted historical collector sources are registered in:

- `knowledge/sources/promoted-sessions.json`;
- `schemas/promoted-session-index.schema.json`.

The promoted registry contains only stable source identity and documented metadata. Raw historical JSONL/session bytes remain external to Git.

Repository validation checks the registry schemas and prevents source-id collisions. Runtime Core tracing additionally cross-checks HAR capture identity against the per-source manifest.

## Core provenance boundary

`bizman.core.trace_evidence()` is the supported application boundary for resolving an evidence ref.

It returns a frozen, path-free `EvidenceTraceResult` containing bounded metadata such as:

- original evidence ref and source id;
- source kind (`har_capture` / `promoted_session`);
- locator kind and ordinal;
- source record/event count;
- whether raw source bytes are committed;
- exact HAR SHA-256 when available;
- runtime session UUIDv7 when available;
- documented observation time/window;
- source privacy statement and provenance policy.

It deliberately does **not**:

- open or return raw HAR entries;
- read historical external JSONL;
- parse prose documentation as runtime truth;
- accept arbitrary filesystem paths;
- expose repository-local paths.

Malformed refs fail at the Core request boundary. A syntactically valid unknown/out-of-range source locator returns no trace. Corrupted or inconsistent provenance assets fail closed through stable Core asset/integrity errors.

## MCP provenance

The read-only MCP adapter exposes `evidence.trace` as a typed structured adaptation of the Core operation.

A normal knowledge-to-proof flow is therefore:

```text
evidence.resolve / evidence.search
  -> evidence ref
  -> evidence.trace
```

This keeps a known item’s provenance reachable in one or two MCP calls without giving the model raw-source or filesystem access.

## Knowledge identities and confidence

Knowledge records use stable IDs:

- `bm.endpoint.*` — HTTP endpoints
- `bm.asset.*` — static resource observations
- `bm.form.*` — HTML form signatures
- `bm.observation.*` — captured events/actions
- `bm.querykey.*` — observed query-key families
- `bm.snapshot.*` — normalized HTML snapshots
- `bm.surfaceprobe.*` — per-surface probe outcome, including
  `bm.surfaceprobe.withheld.*` for state-changing surfaces not requested
- `bm.wiki.*` — Wiki articles
- `bm.product.*` — product entities

Exact request patterns that differ only by query values keep a readable slug and
add a short digest of the pattern, so one surface probed under several query sets
never collapses onto one ID:

```text
bm.surfaceprobe.units-shop--q-id+tab--3f1c9a20
```

A `confidence: observed` record must carry at least one evidence locator. A record
without a locator is `hypothesis`, never `observed`.

Confidence values:

- `observed` — directly present in captured network traffic or HTML.
- `documented` — stated in the captured BizMania Wiki.
- `discovered-reference` — route referenced by captured HTML/JavaScript but not executed in the supplied captures.
- `inferred` / `hypothesis` / `verified` — reserved for research conclusions and must not be silently promoted.
