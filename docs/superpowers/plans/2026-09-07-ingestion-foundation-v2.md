# BizMan Ingestion Foundation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish deterministic IDs, session/redaction contracts and a generic validator so future captures can be ingested without editing snapshot constants.

**Architecture:** Keep operational data outside Git. Add a small Python foundation package under `tools/`, schema/config contracts under `schemas/` and `config/`, and turn `tools/validate_repo.py` into a thin CLI over generic validation functions.

**Tech Stack:** Python 3.11+ compatible tooling (Python 3.14 native UUIDv7 when available), `jsonschema==4.26.0`, JSON Schema Draft 2020-12, SHA-256.

**Spec:** `docs/superpowers/specs/2026-09-07-ingestion-foundation-v2-design.md`

## Global Constraints

- Raw HAR/CDP, cookies, browser profiles, SQLite, DuckDB and Parquet remain outside Git.
- GitHub Actions stay manual-only.
- Existing source provenance must remain resolvable through canonical IDs or explicit aliases.
- No collector, automation, SQLite or Parquet implementation in this phase.

---

### Task 1: Foundation unit contracts

**Files:**
- Create: `tools/bizman_foundation/fingerprint.py`
- Create: `tools/bizman_foundation/redaction.py`
- Create: `tools/bizman_foundation/session.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Produces: `canonical_sha256(value) -> str`, `RedactionPolicy`, `load_redaction_policy(path)`, `redact_headers`, `redact_mapping`, `new_uuid7()`, `new_session_manifest(...)`.

- [ ] Write failing tests for deterministic fingerprints, recursive redaction and UUIDv7 session manifests.
- [ ] Run `python -m unittest discover -s tests -v` and confirm failures are caused by missing foundation modules.
- [ ] Implement the minimal modules.
- [ ] Re-run the tests and confirm they pass.

### Task 2: Generic validator v2

**Files:**
- Create: `tools/bizman_foundation/validation.py`
- Replace: `tools/validate_repo.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Produces: `ValidationResult`, `validate_repository(root: Path) -> ValidationResult`.

- [ ] Write failing tests proving counts are derived from manifests, malformed schemas are rejected, source IDs are consistent, forbidden files are rejected and source instances are schema-validated.
- [ ] Implement catalog-driven validation with no corpus-size constants.
- [ ] Re-run the full unit suite.

### Task 3: Schema and redaction contracts

**Files:**
- Create: `schemas/session-manifest.schema.json`
- Create: `schemas/event.schema.json`
- Create: `schemas/capture-index.schema.json`
- Create: `schemas/redaction-policy.schema.json`
- Modify: `schemas/source.schema.json`
- Modify: `schemas/endpoint.schema.json`
- Modify: `schemas/observation.schema.json`
- Create: `config/redaction-policy.json`
- Create: `tools/requirements.txt`

- [ ] Validate every schema with `Draft202012Validator.check_schema`.
- [ ] Validate the default redaction policy and current source manifests against their schemas.

### Task 4: Source identity migration

**Files:**
- Modify: `knowledge/sources/captures.json`
- Modify: `knowledge/catalog.json`

- [ ] Replace legacy `har:<filename>` canonical IDs with IDs from individual `*.har.json` source manifests.
- [ ] Preserve the old IDs in `legacy_source_ids`.
- [ ] Add `discovered-reference` to the catalog confidence vocabulary for consistency with repository documentation.

### Task 5: Manual validation workflow and docs

**Files:**
- Modify: `.github/workflows/manual-validate.yml`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CONTRIBUTING.md`

- [ ] Keep `workflow_dispatch` as the only trigger.
- [ ] Set up Python 3.14 with a SHA-pinned `actions/setup-python` release.
- [ ] Install `tools/requirements.txt`, run unit tests, then run `python tools/validate_repo.py`.
- [ ] Document the local install/test/validation commands and next CDP phase.

### Task 6: Verification

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python tools/validate_repo.py` against the full checkout.
- [ ] Inspect the branch diff for raw/session/auth/DB artifacts.
- [ ] Open a PR only after fresh verification evidence is available.
