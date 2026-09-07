# BizMan ingestion foundation v2 design

## Goal

Prepare the existing curated BizMan knowledge base for repeatable live ingestion without changing the repository into an operational data store. This phase establishes contracts and validation only; the CDP collector itself is the next phase.

## Constraints

- Git remains curated knowledge and code only. Raw HAR/CDP streams, cookies, browser profiles, SQLite, DuckDB and Parquet remain outside the worktree.
- Collection is read-only by default. Write automation remains out of scope.
- GitHub Actions remain manual-only to preserve private-repository CI quota.
- Existing provenance must remain traceable; no historical source identifier is silently discarded.
- Tooling remains small and deterministic. Python standard library is preferred; `jsonschema==4.26.0` is the only runtime dependency in this phase.

## Runtime identifiers

Runtime `session_id` and future `event_id` use UUIDv7. Python 3.14 provides `uuid.uuid7()` per RFC 9562. Tooling uses the stdlib implementation when available and a small RFC 9562-compatible fallback for older local Python versions so repository validation is not blocked by the interpreter upgrade.

Curated knowledge retains stable `bm.*` identifiers. Source manifests retain stable `src.*` identifiers. These are different namespaces with different lifecycle semantics.

## Source identity migration

`knowledge/sources/*.har.json` is authoritative for canonical source IDs. `knowledge/sources/captures.json` uses those same IDs. Previous `har:<filename>` IDs move to `legacy_source_ids` aliases so old external notes remain resolvable without keeping two competing canonical forms.

## Session contract

A session manifest records UUIDv7 session ID, UTC timestamps, collector version, browser product/version, CDP protocol version, event-file references and artifact count. Browser/CDP version metadata is mandatory because tip-of-tree CDP has no compatibility guarantee.

## Redaction contract

Capture-time sanitization happens before durable normalized events are written. Authorization/cookie/API-token/CSRF/session-like values are dropped case-insensitively. Body capture is first-party only by default and size-bounded. A full forensic capture is not part of the normal event stream and is not committed.

## Fingerprints

Fingerprints use SHA-256 over deterministic UTF-8 JSON (`sort_keys`, minimal separators, no NaN/Infinity). This phase intentionally does not claim RFC 8785/JCS compliance because all producers are Python and adding another dependency solely for cross-language number canonicalization is not justified yet. If a second producer language is introduced, JCS becomes the migration target.

## Validator v2

The validator no longer contains snapshot constants such as 3 captures or 555 events. Counts come from `knowledge/catalog.json` and each partition manifest. It validates:

- catalog paths and declared record counts;
- partition totals, offsets and physical record counts;
- Draft 2020-12 schema correctness;
- source manifests against `source.schema.json`;
- canonical source identity consistency and alias uniqueness;
- forbidden operational/raw files.

The validator remains runnable locally and via the existing manual workflow.

## Technology basis

- Python UUID documentation: https://docs.python.org/3/library/uuid.html
- Python hashlib documentation: https://docs.python.org/3/library/hashlib.html
- JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12
- RFC 9562 UUIDs: https://www.rfc-editor.org/rfc/rfc9562.html
- Chrome DevTools Protocol: https://chromedevtools.github.io/devtools-protocol/
- Chrome remote-debugging profile requirement: https://developer.chrome.com/blog/remote-debugging-port
- OpenTelemetry HTTP semantic conventions: https://opentelemetry.io/docs/specs/semconv/http/

## Out of scope

CDP socket implementation, DOM/BAS action correlation, SQLite projections, Parquet/DuckDB history, change detection, experiments, recommendations and write automation are explicitly deferred to later phases.
