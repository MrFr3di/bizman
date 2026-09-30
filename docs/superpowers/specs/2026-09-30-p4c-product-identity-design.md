# P4-C — Verified Product Identity and Unit-Product Current State

**Status:** revised design for review  
**Date:** 2026-09-30  
**Depends on:** P4-B / PR #39 / merge `5e02b3c378cb6637ef5bb2b2caf7e64375786155`  
**Tracking:** issue #40

## 1. Goal and trust boundary

P4-C extends Current State from verified company/unit identity to a verified association between an already verified unit and products observed on that unit's first-party goods surface.

The authoritative fact is narrowly defined:

> observation O proves that unit U exposed first-party product identity P on approved read surface S.

P4-C does not claim that P is in stock, purchasable, supplied, profitable, currently priced at any value, or absent merely because another page did not show it.

Immutable evidence remains the source of truth. `current.sqlite3` remains a rebuildable projection/cache. The curated product catalog remains knowledge and never becomes live state merely because an entry exists.

## 2. Repository facts that constrain the design

The curated catalog under `knowledge/domain/products` contains 303 products. Only 64 currently have observed numeric IDs; 239 do not. Duplicate display names exist for `Настольный компьютер` and `Смартфон`. Therefore display name, localized label, slug, catalog ordinal and page-row ordinal are prohibited as authoritative identity keys.

Historical `knowledge/pages` contains shop goods/supply and service goods examples, but committed normalized text does not prove where a stable product ID exists in the original response. These files are parser/research evidence only and never Current State input.

P4-B intentionally allows response-body capture only for `GET /company/?id=...&tab=units` and strips arbitrary attributes/form values. P4-C must not silently broaden that generic sanitizer.

The current projection version is 2. P4-C may advance it to 3 only after the C0 evidence gate succeeds.

## 3. External engineering constraints adopted by P4-C

### 3.1 CDP capability, not accidental browser behavior

Chrome DevTools Protocol tip-of-tree changes frequently and has no guaranteed backwards compatibility. C0 records the browser product/version and protocol capabilities used to establish the contract. Runtime code treats `Network.getResponseBody` failure as missing evidence, never as an empty page.

The collector continues to request response content only after the corresponding request/response lifecycle has identified a candidate. Request identity remains scoped to its CDP target/session; a bare `requestId` is never globally unique in BizMan semantics.

### 3.2 Positive validation

All externally derived fields use positive allowlists: exact method, first-party origin, normalized path, allowed query keys/values, status, MIME family, integer grammar/range, field length and row count. Denylists are defense-in-depth only.

URLs are parsed once into structured components before policy decisions. Matching is performed on normalized components rather than substring/regex matching against an unparsed URL. Userinfo, fragments, duplicate security-sensitive query keys, unexpected query keys and non-HTTPS/non-approved origins fail closed.

### 3.3 Structured extraction instead of generic sanitization

P4-C does not persist sanitized generic HTML. A purpose-built parser consumes the response body transiently and emits a small typed product-evidence document. Only specifically proven element/attribute locations may contribute fields.

DOM `id`/`name` values, arbitrary `data-*` attributes, hidden form inputs and document globals are untrusted unless C0 explicitly proves one exact field is the authoritative product identifier and the design is amended. This avoids treating DOM-clobberable names or unrelated hidden state as identity.

Scripts, styles, templates, SVG/foreign content, comments and executable/active content never contribute evidence. Visible labels are context only and never identity.

### 3.4 Canonical evidence

The normalized evidence format is an I-JSON-compatible subset: objects, arrays, UTF-8 strings, booleans and bounded integers; no NaN/Infinity/floats and no duplicate object keys. Canonical serialization uses deterministic UTF-8, sorted object keys and fixed separators. The profile is versioned as `bizman.product-evidence.v1`.

We do not add a JCS dependency for this restricted schema. The format deliberately remains within a subset whose current Python canonical serializer can reproduce exactly. Golden byte fixtures pin the representation.

## 4. C0 — mandatory evidence gate

C0 is a go/no-go experiment, not schema implementation.

A locally authorized passive real-Chrome probe must establish:

1. an approved first-party read surface contains a stable numeric product identifier;
2. the identifier is attached to the correct product row by a structural relation, not merely present elsewhere in the document;
3. extraction requires only an explicitly allowlisted field/location and does not require persisting raw HTML, cookies, tokens, arbitrary attributes or user-authored text;
4. at least two products across at least two units can be observed without ambiguity;
5. repeated observation preserves product identity when row order changes; label rename/localization is tested when feasible;
6. pagination/completeness semantics are measured rather than inferred;
7. malformed/duplicate identifiers can be distinguished from valid rows;
8. browser/CDP version and exact route shape are recorded.

The preferred probe set is:

- shop A, `tab=goods`, at least two rows;
- the same shop after reload/reorder;
- shop B, `tab=goods`;
- same-unit `tab=supply` only as a comparison surface, not automatically approved;
- a paginated goods surface if one exists.

Raw probe bodies remain local and ephemeral. They are not pasted into GitHub issues, CI logs or committed fixtures.

C0 produces `docs/research/p4c-product-evidence.md` containing only safe field-level examples, route grammar, structural selector description, browser/protocol metadata, ambiguity tests, pagination findings, redaction decisions and the go/no-go conclusion.

### C0 stop rule

If no stable product identifier can be safely and deterministically extracted, P4-C stops before schema v3. The allowed deliverable is a documented evidence gap and, if useful, an explicitly non-authoritative research parser. Name matching, ordinal matching and inferred IDs are not fallback strategies.

## 5. Product evidence v1 contract

The exact extraction location is frozen only after C0. The normalized artifact shape is nevertheless bounded now:

- `schema`: exactly `bizman.product-evidence.v1`;
- `unit_id`: positive decimal integer from the approved request identity;
- `surface`: closed enum containing only independently proven surfaces;
- `rows`: ordered only for serialization; semantic identity is not row order;
- each row contains `product_numeric_id` and optionally `observed_label`;
- `coverage`: closed enum whose values are enabled only when C0 proves their meaning;
- `source`: normalized path/query identity, not the full URL and never credentials.

Hard limits are part of the contract, not implementation accidents:

- response body: retain the existing P4 bounded-body ceiling unless C0 demonstrates a smaller safe limit;
- product rows: maximum 512 per artifact;
- observed label: maximum 256 Unicode scalar values;
- numeric IDs: positive and bounded to signed 64-bit range;
- duplicate product IDs in one artifact: reject the artifact;
- duplicate JSON keys: reject before canonicalization.

A product row is accepted atomically. A malformed row does not get silently dropped while sibling rows become authoritative; structural incompatibility makes the artifact incompatible/stale.

## 6. Capture pipeline

The approved flow is:

```text
Network request/response lifecycle
  -> target-scoped candidate metadata
  -> strict structured URL/origin/method/status/MIME policy
  -> bounded Network.getResponseBody
  -> decode with explicit size ceiling
  -> transient structural extractor
  -> typed ProductEvidenceV1 validation
  -> canonical JSON bytes
  -> SHA-256 CAS
  -> immutable http.response_body-derived evidence event
```

The existing P4-B artifact and parser remain replay-compatible and unchanged.

The event must distinguish artifact kind/schema so replay never guesses parser type from URL text. Capture warnings identify failure class without embedding response content.

Supply, service and future product surfaces require separate evidence decisions. Similar markup is not sufficient to inherit trust.

## 7. Identity and catalog resolution

P4-C has three separate identities:

1. **Observed product identity** — first-party numeric ID from verified evidence.
2. **Curated catalog identity** — stable `bm.product.*` knowledge key.
3. **Unit-product association** — `(unit_id, observed_numeric_product_id)`.

Observed identity is authoritative for the live observation. Catalog resolution is nullable derived metadata.

Resolution rules:

- numeric-ID mapping may resolve one observed ID to exactly one catalog key;
- zero candidates => unresolved;
- multiple candidates => ambiguous/unresolved and surfaced explicitly;
- display label can be retained for diagnostics but never breaks a tie;
- catalog mapping changes never rewrite immutable evidence.

The catalog resolver gets a semantic fingerprint computed from the exact normalized mapping inputs that can affect resolution. File paths, mtimes and JSON formatting do not affect it. Mapping content and resolver-version changes do.

## 8. Provenance model

Every projected association retains:

- `session_id`;
- `event_sequence`;
- canonical `observed_at`;
- artifact SHA-256;
- evidence schema version;
- unit ID;
- observed numeric product ID;
- surface.

This follows an entity/derivation style: the projected row is derivable back to one immutable evidence entity. BizMan does not need RDF/PROV dependencies; the useful property is explicit derivation, not adoption of another storage format.

A provenance reference that cannot be resolved through `EvidenceReader.read_verified_artifact` is invalid.

## 9. Reducer and uncertainty semantics

Replay order remains `(session.started_at, session_id, event.sequence)`.

A verified positive observation adds or refreshes `(unit_id, product_numeric_id)`.

The following are UNKNOWN and never deletion:

- product omitted from a partial page;
- pagination not fully observed;
- missing page;
- failed body capture;
- inaccessible response body;
- unsupported surface;
- parser-incompatible artifact.

Deletion/removal is outside P4-C. It requires a later separately proven complete-snapshot/tombstone contract.

Coverage is per unit and per surface, separate from P4-B company/unit coverage. Minimum states:

- `unknown`: no sufficient compatible evidence;
- `ready`: sufficient compatible positive evidence for the semantics claimed;
- `stale`: relevant evidence exists but parser/structure contract is incompatible.

A unit roster being ready does not imply its product roster is ready.

If product evidence references a unit that has no verified P4-B unit row, the observation remains explainable but cannot become an authoritative FK-backed unit-product association. Replay surfaces deterministic unresolved/orphan coverage; it must not join by unit name or discard the observation invisibly.

## 10. Current State schema v3

Schema v3 is created only after C0 passes.

The preferred normalized tables are:

- `observed_product`: observed numeric product identity plus nullable catalog resolution state;
- `unit_product`: FK-backed association keyed by `(unit_id, observed_product_id)` with provenance;
- `product_surface_state`: per-unit/per-surface coverage and last compatible observation;
- existing P4-A/P4-B metadata and replay ledger.

Exact SQL is an implementation-plan decision constrained by these invariants:

- STRICT tables;
- explicit PK/UNIQUE/CHECK constraints;
- foreign keys enabled and validated;
- no display-name FK;
- no nullable provenance on authoritative associations;
- canonical ordering in snapshot serialization;
- all product rows and coverage participate in `state_fingerprint`.

`input_fingerprint` includes projection version, existing analysis-profile identity, replayed immutable sessions, product-evidence contract version and catalog-resolution semantic fingerprint. This prevents identical live evidence from producing an apparently identical input identity after resolver semantics change.

## 11. v2 -> v3 rebuild/swap

Current State is derived, so P4-C favors deterministic rebuild over in-place mutation.

For an exactly recognized v2 database:

1. keep v2 untouched;
2. build v3 in a same-directory sibling path from immutable evidence;
3. close/flush the staged writer;
4. run SQLite `integrity_check` and `foreign_key_check` on the staged database;
5. reopen it through the normal Current State validator and verify fingerprints;
6. ensure no live staged WAL/SHM state is required for correctness;
7. atomically replace the target with the staged database using the existing same-filesystem swap discipline;
8. reopen the target and validate identity/fingerprints again.

Crash/fault injection is tested before build completion, after validation and around replacement. Before the replace point, the old v2 remains usable. After replacement, the target must be a valid v3 or recovery must fail closed and rebuild from evidence.

Unknown, foreign and newer schemas are never rewritten.

Do not use `VACUUM INTO` as the migration primitive: SQLite documents it as a consistent snapshot mechanism, but interrupted generation can leave the output incomplete. P4-C already owns a deterministic staged rebuild and can validate that artifact before replacement.

## 12. Transaction compatibility

BizMan supports Python >=3.11. Python's `sqlite3.Connection.autocommit` parameter was introduced in 3.12, so P4-C must not regress the Python 3.11-safe explicit transaction discipline established during P4-B.

Migration/rebuild code must have one explicit transaction ownership model and tests for rollback on body exception, commit failure and close/reopen behavior. It must not mix implicit legacy transactions with explicit `BEGIN` accidentally.

## 13. Threat model

P4-C treats all page/evidence content as untrusted data.

Tests and validation cover:

- hostile/malformed HTML nesting;
- DOM-clobbering `id`/`name` values;
- hidden inputs and script/style/template/SVG content carrying fake IDs;
- duplicate or conflicting product IDs;
- duplicate query keys and unexpected query parameters;
- percent-encoding/canonical URL edge cases;
- same numeric `requestId` on different CDP targets;
- oversized encoded and decoded bodies;
- invalid/base64 decoding;
- duplicate JSON keys;
- Unicode labels/confusables;
- catalog duplicate names and conflicting numeric mappings;
- CAS tampering and DB provenance tampering.

No label normalization is allowed to alter identity because labels are not identity.

## 14. Observability and privacy

Metrics/counters are structural only: candidate accepted/rejected reason, body capture failure class, parser incompatibility class, row count and artifact kind. Logs never include raw response bodies, cookies, authorization headers, arbitrary query strings, hidden values or labels unless an existing explicit safe logging contract allows the exact field.

C0 and CI fixtures seed canary secrets in excluded markup. Repository validation asserts those canaries never appear in JSONL, CAS artifacts, snapshots, logs or committed fixtures.

## 15. Testing strategy

### Contract/unit

Prove URL policy, bounded decoding, structural extraction, canonical bytes, duplicate rejection, row limits and catalog resolution independently.

### Reducer

Prove:

- identity survives rename/localization;
- duplicate catalog names never affect authoritative identity;
- unknown catalog mapping preserves observed identity;
- orphan unit evidence does not cross-join;
- partial pages never delete;
- parser drift yields stale coverage;
- replay order is deterministic;
- identical evidence + catalog semantics yields identical fingerprints;
- catalog mapping/resolver version change changes input/state fingerprints.

### Persistence/migration

Prove STRICT/FK/check constraints, tamper detection, exact-v2 recognition, unknown/newer rejection, staged validation, fault-injected swap recovery, and deterministic delete/rebuild.

### Real Chrome E2E

Only after C0 freezes the structural contract, the fixture reproduces that proven structure with:

- two products;
- two units across the scenario;
- reordered rows;
- seeded hidden/script/attribute secrets;
- target/request collision coverage;
- response-body failure path.

E2E verifies JSONL/CAS privacy, projected provenance and exact delete/replay fingerprints.

### Compatibility and quality

Final HEAD must pass Python 3.11 and 3.14 lanes, Ruff, import boundaries, repository privacy validation, full tests/coverage, real Chrome E2E, benchmark, Sonar Quality Gate and blocking review resolution.

## 16. Alternatives considered

### A. Name-based join

Rejected. Existing duplicate names and localization/rename make it non-authoritative.

### B. Persist generic sanitized HTML and parse later

Rejected for P4-C. It broadens the privacy surface, preserves more untrusted structure than required and makes the evidence contract harder to audit.

### C. Capture arbitrary hidden form values

Rejected unless C0 proves one exact field is the authoritative ID and the design is explicitly amended. Hidden state can contain unrelated operational or secret values.

### D. Build schema v3 first and fill identity later

Rejected. It encodes uncertainty as schema truth and creates migration work before the source contract is known.

### E. Keep product catalog and live product state in one table

Rejected. Curated knowledge and observed operational facts have different provenance, lifetimes and uncertainty.

## 17. Acceptance and stop conditions

P4-C is accepted only when:

- C0 proves a stable, safe, row-bound first-party numeric product ID;
- capture persists only typed allowlisted product evidence;
- immutable artifact provenance is verifiable end-to-end;
- Current State preserves observed identity separately from catalog resolution;
- UNKNOWN/STALE semantics prevent omission-driven deletion;
- v2 -> v3 rebuild is staged, validated and fault-tested;
- fingerprints bind every semantic input that can alter projection output;
- deterministic replay and privacy canaries pass in real Chrome E2E;
- final CI/Sonar/review gates are green.

If C0 fails, the correct outcome is a documented evidence gap. P4-C must not manufacture certainty to satisfy a milestone.

## 18. Engineering references reviewed (2026-09-30)

- Chrome DevTools Protocol overview/versioning: https://chromedevtools.github.io/devtools-protocol/
- CDP Network domain / `Network.getResponseBody`: https://chromedevtools.github.io/devtools-protocol/tot/Network/
- WHATWG URL Living Standard (updated 2026-09-10): https://url.spec.whatwg.org/
- OWASP Input Validation Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- OWASP DOM Clobbering Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/DOM_Clobbering_Prevention_Cheat_Sheet.html
- RFC 8785 JSON Canonicalization Scheme (used as design reference; no new dependency): https://www.rfc-editor.org/rfc/rfc8785.html
- SQLite atomic commit: https://www.sqlite.org/atomiccommit.html
- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite PRAGMA integrity/foreign-key checks: https://www.sqlite.org/pragma.html
- SQLite VACUUM INTO semantics: https://www.sqlite.org/lang_vacuum.html
- Python sqlite3 transaction control: https://docs.python.org/3/library/sqlite3.html
- W3C PROV overview (conceptual provenance reference only): https://www.w3.org/TR/prov-overview/
