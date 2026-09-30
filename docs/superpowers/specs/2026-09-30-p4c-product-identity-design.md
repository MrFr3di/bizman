# P4-C — Verified Product Identity and Unit-Product Current State

**Status:** design for review  
**Date:** 2026-09-30  
**Depends on:** P4-B / PR #39 / merge `5e02b3c378cb6637ef5bb2b2caf7e64375786155`  
**Tracking:** issue #40

## 1. Goal

P4-C extends Current State from verified company/unit identity to a verified association between a concrete unit and the products observed on that unit's first-party goods surface. The result must be deterministic, rebuildable from immutable evidence, privacy-bounded, and explicit about uncertainty.

The authoritative fact produced by P4-C is **unit X was observed offering product Y at observation O**. The curated product catalog remains a separate knowledge source and is never treated as live operational state merely because a product exists in the catalog.

## 2. Non-goals

P4-C does not model inventory quantities, stock depletion, procurement, vendor relationships, sale prices, margins, product quality, orders, recommendations, write automation, or generic HTML scraping. It does not force all curated products into Current State. A POST/write request never becomes an authoritative current-state observation without a subsequent verified read.

## 3. Current repository evidence

The repository contains 303 curated products under `knowledge/domain/products`. Only 64 currently have unique observed numeric IDs; 239 do not. `Настольный компьютер` and `Смартфон` have duplicate display-name occurrences. Therefore display name, catalog ordinal, page row ordinal, or slug is not an authoritative product identity.

Historical `knowledge/pages` examples include shop goods and supply surfaces, but their normalized text does not prove the location or stability of product IDs in the original DOM/response. P4-B intentionally strips arbitrary HTML attributes and only permits the proven company-units response capture. P4-C must establish its own evidence contract before changing that allowlist.

## 4. Mandatory C0 evidence gate

Before schema-v3 work or authoritative materialization, a passive real-browser probe must establish all of the following:

1. The first-party goods surface has a stable product identifier.
2. The identifier can be extracted using an explicit allowlisted field/path without persisting raw HTML, arbitrary DOM attributes, hidden secrets, cookies, tokens, or unrelated user text.
3. The identifier is associated with the correct product row, not merely present somewhere on the page.
4. The same identifier remains stable across at least two observations, including a renamed/localized display label where feasible.
5. At least two products on at least two units can be mapped without ambiguity.
6. Reordering/pagination, if the surface supports it, does not change identity semantics.

If these conditions cannot be demonstrated, P4-C stops after the evidence report and does not introduce an authoritative product-ID field. A name-only or ordinal-based join is explicitly prohibited.

The raw browser captures used for C0 remain local and are never committed to GitHub. Only sanitized field-level observations and schema fixtures may enter the repository.

## 5. Evidence contract

Once C0 succeeds, P4-C introduces a dedicated product-evidence contract rather than broadening the generic P4-B HTML sanitizer. The contract contains only bounded typed fields whose semantics have been proven by C0.

Conceptually each normalized observation contains:

- `unit_id`: positive authoritative unit ID already bound by P4-B;
- `product_numeric_id`: positive first-party product ID;
- `observed_label`: optional bounded display label, retained as observation context rather than identity;
- `source_session_id` and `source_event_sequence`: immutable provenance;
- `observed_at`: event observation timestamp;
- `surface`: the proven goods surface identifier;
- `coverage`: only a value whose completeness semantics were explicitly proven.

The exact JSON schema version is frozen only after C0 establishes the real field names. Unknown or ambiguous fields are omitted rather than guessed.

## 6. Collection and isolation

The collector remains passive. Only explicitly approved successful GET resources are captured. A candidate is accepted only when request target, method, status, content type, target correlation, and encoded/decoded size limits satisfy the contract.

The goods surface must be allowlisted separately from the P4-B company-units surface. Supply/service routes are separate capabilities and require independent evidence; they are not implicitly accepted because they look similar.

Per-target request isolation remains mandatory. A product response belonging to unit A must never be attached to unit B merely because the page text or navigation name matches.

Sanitization must reject malformed structure, duplicate product IDs within a complete observed page, hidden/nested content that attempts to smuggle fields, source URL spoofing, unexpected encodings, and changed row structure. Tampered content-addressed artifacts continue to fail closed through the existing verified evidence reader.

## 7. Domain model

The domain distinguishes three layers:

1. **Curated Product Catalog** — static knowledge such as the 303 known products and optional numeric-ID mappings.
2. **Observed Product Identity** — a first-party numeric product ID captured from a verified read.
3. **Unit-Product Association** — a derived Current State fact connecting an already verified unit to an observed product identity.

The primary association identity is `(unit_id, observed_numeric_product_id)`. Catalog resolution is nullable and separate. If a numeric product ID cannot be mapped to a curated catalog item, the observed fact remains valid with an unresolved catalog mapping. If two catalog candidates match, the mapping is ambiguous and must remain unresolved.

The system must not silently overwrite an observed product identity with a catalog identity, and it must not use catalog display names as foreign keys.

## 8. Projection semantics

P4-C replays immutable evidence in the existing deterministic order `(session.started_at, session_id, event.sequence)`.

A positive verified product observation adds or refreshes the corresponding `(unit_id, product_numeric_id)` association. A missing row, partial page, failed response capture, absent page, or pagination gap means **UNKNOWN**, not deletion.

Deletion semantics are deliberately excluded from this slice. A later stage may introduce removal only after an authoritative complete-snapshot contract is proven.

Projection coverage is tracked independently for the company roster and product roster. A unit can therefore be verified while its product roster is `unknown` or `stale`. Partial product evidence must never make the overall unit appear fully product-verified.

Parser incompatibility and evidence corruption are distinct failure classes. Parser drift must not publish a partial authoritative projection.

## 9. Current State schema evolution

The product projection advances Current State from schema/projection v2 to v3 only after C0 succeeds.

The v3 database uses strict tables and foreign-key/provenance validation. Product associations carry enough provenance to explain which immutable observation produced each current row.

The canonical state identity includes the product-evidence projection version and the semantic fingerprint of the curated product mapping. Therefore changing catalog identity resolution changes the input/state fingerprint even when the underlying live evidence is identical.

For an exactly recognized v2 database, migration is performed by building a fully validated sibling v3 database and atomically replacing the old database. WAL/SHM safety must be handled explicitly. If a crash occurs before replacement, the v2 database remains intact. Unknown, newer, or foreign schemas fail closed.

The derived v3 state must remain completely rebuildable from immutable evidence; the database is a cache of the projection, not a source of truth.

## 10. Error and uncertainty model

The implementation distinguishes:

- verified product observation;
- unresolved catalog mapping;
- ambiguous catalog mapping;
- unknown product coverage;
- stale product coverage;
- parser incompatibility;
- malformed/tampered evidence;
- target/request correlation failure.

No error path may silently downgrade to name matching, row ordinal, or best-effort guessing.

## 11. Testing and acceptance

Unit and integration coverage must prove:

- numeric product identity survives display-name rename/localization;
- duplicate catalog names never create an ambiguous authoritative join;
- absence of a product ID yields unknown/unresolved state rather than a fabricated ID;
- missing unit roster prevents accidental cross-unit association;
- partial pages never delete previously verified products;
- duplicate/conflicting IDs and parser drift fail closed;
- tampered CAS and invalid provenance fail closed;
- catalog mapping changes alter the canonical input/state fingerprint;
- replaying identical evidence twice produces byte/semantic deterministic output;
- v2-to-v3 migration is atomic and crash-safe;
- real Chrome E2E captures only the allowlisted product fields and does not persist seeded secrets.

Final acceptance requires Python 3.11 and 3.14 compatibility, Ruff/import-boundary checks, repository privacy checks, real Chrome E2E, benchmark, GitHub Actions green, Sonar Quality Gate green, and no unresolved blocking review threads.

## 12. Explicit stop conditions

Implementation must stop before authoritative product projection if:

- no stable first-party product identifier can be safely captured;
- the identifier can only be obtained from unrestricted/raw HTML attributes or hidden form state that is not covered by the evidence contract;
- unit-to-product association cannot be correlated deterministically;
- completeness semantics of a supposed full roster cannot be demonstrated.

In that case the deliverable is a documented evidence gap and an experimental/non-authoritative parser, not a schema change that manufactures certainty.
