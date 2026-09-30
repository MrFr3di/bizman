# P4-C Verified Product Identity — Implementation Plan

> **Execution rule:** follow this plan task-by-task with TDD. Do not implement schema v3 until Task 3 (C0 evidence gate) has a documented PASS.

**Goal:** extend replayable Current State with trustworthy unit-product associations derived from narrowly allowlisted immutable first-party evidence, without name joins, omission-driven deletion, generic HTML persistence, or write inference.

**Architecture:** C0 first establishes a safe row-bound numeric product identity from real Chrome evidence. Only then a purpose-built capture/extraction path emits canonical ProductEvidenceV1 artifacts. Replay preserves observed identity independently from curated catalog resolution, then schema v3 materializes FK-backed associations and per-surface uncertainty with deterministic fingerprints and staged migration.

**Stack:** Python >=3.11, stdlib HTML/URL/sqlite3/hashlib/json, existing CDP collector/CAS/EvidenceReader, jsonschema, unittest, Ruff, Import Linter, GitHub Actions/Sonar.

---

## Task 1 — Freeze C0 research contract and safe probe model

**Files:**
- Create: `docs/research/p4c-product-evidence.md`
- Create: `src/bizman/collector/product_probe.py`
- Test: `tests/test_p4c_product_probe.py`

**Step 1: write failing tests**

Tests must prove that the research parser:
- accepts only an explicit `https://<approved-first-party>/units/shop/?id=<positive>&tab=goods` shape;
- rejects HTTP, userinfo, fragments, duplicate `id`/`tab`, unknown query keys, zero/negative/non-ASCII IDs, other unit types/tabs;
- has no durable-output API for raw HTML;
- reports candidate identity locations structurally without treating labels/row ordinals as IDs.

**Step 2: run the focused test and verify RED**

`python -m unittest tests.test_p4c_product_probe -v`

**Step 3: implement minimum research-only primitives**

Add a small URL policy/value object and a transient HTML inspection model. Keep it independent of Current State and production body persistence. It may report only bounded structural metadata needed to determine whether an exact href/query/attribute location can become the C0 contract.

**Step 4: run focused tests and verify GREEN**

**Step 5: commit**

`test(p4c): establish fail-closed product evidence probe contract`

## Task 2 — Add local C0 probe command without widening production collection

**Files:**
- Modify: `src/bizman/cli/main.py`
- Modify/create the relevant Core use-case module only if required by import boundaries.
- Test: `tests/test_cli.py`
- Modify: `docs/research/p4c-product-evidence.md`

**Step 1: failing tests**

Specify an explicitly research-labelled command that consumes a user-provided local response-body file plus its source URL. It must:
- never make a write request;
- never persist/copy the raw body;
- print only bounded structural findings;
- redact/omit text values not needed for identity research;
- return nonzero for unsupported route or ambiguous identity candidates.

This enables C0 on a locally captured authorized page without committing secrets and without prematurely enabling runtime collection.

**Step 2:** run CLI contract tests and verify RED.

**Step 3:** implement minimal command/use case.

**Step 4:** run tests and repository privacy validation.

**Step 5:** commit `feat(p4c): add local product identity evidence probe`.

## Task 3 — Execute C0 against real authorized evidence and make GO/NO-GO decision

**Files:**
- Modify: `docs/research/p4c-product-evidence.md`
- No production schema files.

**Procedure:**
1. Capture locally, outside Git, approved real first-party `GET /units/shop/?id=<unit>&tab=goods` response(s).
2. Record Chrome product/version and protocol metadata.
3. Probe shop A with >=2 rows, reload/reorder, shop B, and pagination if present.
4. Verify exact structural relation between each product row and numeric ID.
5. Verify that no cookie/token/form secret is needed.
6. Record only safe field-level examples and a PASS/FAIL matrix.
7. If identity is not proven: mark C0 NO-GO, open an evidence-gap follow-up and STOP Tasks 4+.
8. If identity is proven: freeze the exact extractor contract and continue.

**Verification:** a reviewer must be able to reproduce the decision without receiving raw authenticated HTML.

**Commit:** `docs(p4c): record product identity evidence gate result`.

## Task 4 — ProductEvidenceV1 canonical artifact contract (only after C0 PASS)

**Files:**
- Create: `schemas/product-evidence-v1.schema.json`
- Create: `src/bizman/collector/product_evidence.py`
- Test: `tests/test_p4c_product_evidence.py`

**Tests first:** exact schema tag, positive int64 IDs, <=512 rows, <=256-scalar labels, duplicate IDs rejected, duplicate JSON keys rejected on read, deterministic bytes under row reorder policy, no floats/non-finite numbers, unexpected fields rejected.

Implement typed immutable values plus one canonical serializer. Add golden-byte fixtures in tests; do not add JCS dependency.

Commit: `feat(p4c): define canonical product evidence v1`.

## Task 5 — Narrow production capture/extraction

**Files:**
- Modify: `src/bizman/collector/network.py`
- Modify: `src/bizman/collector/runtime.py`
- Modify: `tests/test_p4b_response_capture.py`
- Create/modify: `tests/test_p4c_response_capture.py`
- Modify: `tools/ci/fixture_server.py`

**Tests first:** exact route policy, target-scoped request identity, status/MIME, encoded+decoded limits, body failure => warning/UNKNOWN, hostile hidden/script/template/SVG markup, fake IDs in unapproved attributes, duplicate IDs, unexpected query keys, percent-encoding, reordered rows, privacy canaries.

Implement separate candidate kind and extractor. Do not mutate P4-B sanitizer v2 bytes/semantics. Artifact kind/schema must be explicit; runtime warning text becomes surface-neutral or typed.

Commit: `feat(p4c): capture typed unit product evidence`.

## Task 6 — Curated catalog resolver and semantic fingerprint

**Files:**
- Create: `src/bizman/current/products.py`
- Test: `tests/test_current_products.py`

**Tests first:** unique numeric ID resolves, no ID unresolved, duplicate mapping ambiguous, duplicate display names irrelevant, labels never break ties, formatting/path/mtime changes do not change semantic hash, mapping/resolver version changes do.

Implement a deterministic resolver over only mapping fields that can affect output. No dependency from collector to current/knowledge application layers.

Commit: `feat(p4c): add deterministic product catalog resolution`.

## Task 7 — Replay model and uncertainty semantics

**Files:**
- Modify: `src/bizman/current/model.py`
- Modify: `src/bizman/current/replay.py`
- Modify: `src/bizman/current/products.py`
- Test: `tests/test_current_products.py`

**Tests first:** positive observation, rename/localization identity stability, partial omission no deletion, parser drift stale, unsupported/missing body unknown, orphan unit deterministic unresolved coverage, no name join, latest-positive replay ordering, artifact SHA provenance.

Implement `ObservedProduct`, `UnitProductState`, per-unit/per-surface coverage and canonical snapshot ordering. Removal semantics remain absent.

Commit: `feat(p4c): replay verified unit product observations`.

## Task 8 — Fingerprint semantics

**Files:**
- Modify: `src/bizman/current/model.py`
- Test: `tests/test_current_products.py`
- Test: `tests/test_current_state.py`

**Tests first:** identical evidence/catalog semantics => identical input/state fingerprints; catalog semantic mapping or resolver version change => changed fingerprint; JSON formatting/path changes => unchanged catalog semantic fingerprint; product coverage/provenance changes => state fingerprint change.

Implement projection v3 fingerprint inputs only after all tests define the contract.

Commit: `feat(p4c): bind product semantics into current state identity`.

## Task 9 — SQLite schema v3 and exact v2 staged rebuild

**Files:**
- Modify: `src/bizman/current/state.py`
- Modify: `src/bizman/current/replay.py`
- Test: `tests/test_current_state.py`
- Test: `tests/test_current_company_units.py`
- Test: `tests/test_current_products.py`

**Tests first:** STRICT PK/FK/CHECKs, provenance required, tamper detection, exact v2 recognized, foreign/unknown/newer rejected, staged sibling built before replace, `integrity_check` and `foreign_key_check` pass, injected failures before/at replace preserve valid recoverable state, Python 3.11 transaction behavior, delete/rebuild deterministic.

Implement schema/user version 3. Keep old DB untouched until fully validated sibling is closed and ready for atomic same-directory replacement.

Commit: `feat(p4c): materialize product current state schema v3`.

## Task 10 — Real Chrome E2E and privacy gate

**Files:**
- Modify: `tools/ci/fixture_server.py`
- Modify: `tools/ci/verify_collector_e2e.py`
- Modify: `.github/workflows/collector-e2e.yml` only if execution plumbing genuinely changes.
- Modify: `docs/architecture/storage.md`

Fixture markup must copy only the safe structural shape proven by C0, not invented markup.

Verify two products/two units, row reorder, hidden/script/attribute canaries, CAS/JSONL/log privacy, provenance, unavailable-body behavior, exact snapshot/fingerprint after deleting and rebuilding Current State.

Commit: `test(p4c): prove product projection in real Chrome replay`.

## Task 11 — Full quality/review gate

Run:
- Python 3.14 validate lane;
- Python 3.11 compatibility lane;
- full unit/coverage suite;
- Ruff;
- Import Linter;
- repository validation/privacy checks;
- real Chrome E2E;
- benchmark;
- Sonar Quality Gate.

Review all Sonar/review findings. Fix correctness/reliability/security findings rather than suppressing them. Record intentionally deferred nonblocking findings.

## Task 12 — Documentation and completion

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `docs/architecture/storage.md`
- Modify: `docs/research/p4c-product-evidence.md`
- Update issue #40 / PR description.

Mark P4-B complete (the roadmap currently still says `current`) and P4-C complete only after the final reviewed HEAD passes every acceptance gate. State explicitly that inventory/stock is the next separate slice.

Final merge is squash after green final HEAD.
