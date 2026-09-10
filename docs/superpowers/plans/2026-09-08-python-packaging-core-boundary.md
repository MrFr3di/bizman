# Python Packaging + Application Core Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert BizMan into an installable `src/bizman` package with a small stable application Core boundary while preserving Collector/Detector semantics exactly.

**Architecture:** Production implementations move out of `tools/` into `src/bizman`. `bizman.core` is the supported application boundary for adapters; foundation/sessions/collector/changes remain implementation packages with enforced dependency direction. Old tool imports and script entry points remain temporary thin compatibility shims.

**Tech Stack:** Python >=3.11 (3.14 primary), uv 0.12.10, uv_build >=0.12.10,<0.13, websockets >=17.1,<18, jsonschema[format] >=4.26,<5, Ruff 0.16.3, Import Linter 2.15, stdlib unittest/dataclasses/typing.Protocol.

**Spec:** `docs/superpowers/specs/2026-09-08-python-packaging-core-boundary-design.md`

## Global Constraints

- Start from `main` merge SHA `c96d96ecfe0cab7fa4d115c1cf757ab5741f28b2`.
- Work only on `feature/python-package-core-v1` until review/merge.
- Immutable sanitized evidence remains source of truth.
- No semantic detector/collector version bump solely for package migration.
- No MCP/Pydantic/FastAPI/DuckDB/Agent Index/Market implementation in P1.
- RED→GREEN for behavior/refactor boundaries; configuration-only creation is verified immediately after creation.
- Never allow `src/bizman` to import `tools.*`.
- Existing deterministic SHA/IDs/bundle bytes and SQLite detector schema must remain equivalent.

---

### Task 1: Freeze architecture and migration identities

**Files:**
- Create: `tests/test_package_migration_contract.py`
- Create: `tests/fixtures/package_migration_golden.json`
- Modify later only after golden capture is reviewed.

**Interfaces:**
- Consumes current `tools.bizman_*` implementation.
- Produces a committed semantic migration fingerprint used by all later tasks.

- [ ] Write a test that constructs the real RuntimeContract/profile and deterministic detector fixture, then compares canonical semantic fingerprints with `tests/fixtures/package_migration_golden.json`.
- [ ] Add collector semantic projection coverage that excludes only explicitly runtime-specific values.
- [ ] Run the new test without a golden file and verify RED because the migration baseline has not been captured.
- [ ] Generate the golden only from the verified current implementation, review that it contains no secret/value-bearing data, commit it, and verify GREEN.
- [ ] Run the full 183-test pre-existing suite plus the new migration tests.
- [ ] Commit: `test: freeze pre-migration semantic identities`.

### Task 2: Add package metadata and locked environment

**Files:**
- Create: `pyproject.toml`
- Create: `src/bizman/__init__.py`
- Create: `uv.lock`
- Test: `tests/test_distribution_contract.py`

**Interfaces:**
- Produces installable package root and deterministic dependency boundary.

- [ ] Add a failing distribution contract asserting `pyproject.toml` declares Python >=3.11, `bizman` console script, uv_build backend, current runtime dependencies and dev-only Ruff/Import Linter.
- [ ] Verify RED because package metadata does not exist.
- [ ] Add minimal `pyproject.toml` and package root; generate `uv.lock` with uv 0.12.10.
- [ ] Run `uv lock --check` and `uv sync --locked`.
- [ ] Run `uv build --no-sources` and verify wheel/sdist creation.
- [ ] Verify distribution contract GREEN.
- [ ] Commit: `build: establish locked src package`.

### Task 3: Migrate foundation package

**Files:**
- Create: `src/bizman/foundation/*` equivalents of current foundation modules.
- Modify: `tools/bizman_foundation/*` into compatibility re-exports.
- Test: `tests/test_package_compatibility.py`

**Interfaces:**
- Produces canonical `bizman.foundation` objects.

- [ ] Write RED identity tests proving old/new imports must resolve to the same key class/function objects.
- [ ] Copy/move canonical foundation implementation to `src/bizman/foundation` and switch internal canonical imports.
- [ ] Replace old modules with thin re-exports; do not duplicate class definitions.
- [ ] Run foundation, validator and package compatibility tests GREEN.
- [ ] Run migration golden tests unchanged.
- [ ] Commit: `refactor: migrate foundation into package`.

### Task 4: Establish sessions boundary

**Files:**
- Create: `src/bizman/sessions/evidence.py`
- Create: `src/bizman/sessions/status.py`
- Modify: detector imports and compatibility modules.
- Test: existing evidence tests + compatibility tests.

**Interfaces:**
- Produces canonical `EvidenceReader`, `EvidenceIdentity`, `EvidenceSessionStatus` under `bizman.sessions`.

- [ ] Add RED identity/import tests for the new sessions namespace.
- [ ] Move evidence/status implementation without changing schemas, hashes, traversal rules, CAS verification or streaming limits.
- [ ] Point legacy detector namespace to canonical sessions objects where compatibility requires it.
- [ ] Run evidence integrity/path/CAS tests and migration golden GREEN.
- [ ] Commit: `refactor: separate sessions evidence boundary`.

### Task 5: Migrate Collector implementation

**Files:**
- Create: `src/bizman/collector/*`
- Modify: `tools/bizman_collector/*` compatibility modules.
- Test: collector unit/E2E semantic fixtures.

**Interfaces:**
- Produces canonical collector implementation and `run_collection` equivalent.

- [ ] Add RED import/identity tests for canonical collector namespace.
- [ ] Migrate modules mechanically and update imports to `bizman.foundation`.
- [ ] Keep legacy namespace re-export-only.
- [ ] Compare deterministic collector semantic projection with pre-migration golden.
- [ ] Run all collector tests GREEN.
- [ ] Commit: `refactor: migrate collector into package`.

### Task 6: Migrate Change Detector implementation

**Files:**
- Create: `src/bizman/changes/*`
- Modify: `tools/bizman_detector/*` compatibility modules.
- Test: full detector suite + migration golden.

**Interfaces:**
- Produces canonical changes package while consuming `bizman.sessions` and `bizman.foundation`.

- [ ] Add RED import/identity tests for detector canonical objects.
- [ ] Migrate baseline/diff/extract/model/normalization/promotion/runner/state/rules without semantic changes.
- [ ] Update imports to `bizman.sessions` and `bizman.foundation`.
- [ ] Verify legacy namespace re-exports canonical objects.
- [ ] Verify RuntimeContract/baseline/Profile/change IDs/bundle bytes equal migration golden.
- [ ] Verify detector SQLite application ID/user version/schema unchanged.
- [ ] Run full detector suite GREEN.
- [ ] Commit: `refactor: migrate change detector into package`.

### Task 7: Add typed repository asset registry

**Files:**
- Create: `src/bizman/core/assets.py`
- Test: `tests/test_core_assets.py`

**Interfaces:**
- Produces `AssetId` and `RepositoryAssets.path(AssetId) -> Path`.

- [ ] Write RED tests for exact asset mapping, required asset validation, containment and rejection of non-`AssetId` arbitrary values.
- [ ] Implement `AssetId(StrEnum)` for redaction policy, event schema, session manifest schema, promotion schema and knowledge root.
- [ ] Implement canonical root/containment checks fail-closed.
- [ ] Run tests GREEN.
- [ ] Commit: `feat: add typed repository assets`.

### Task 8: Add UTC application time boundary

**Files:**
- Create: `src/bizman/core/time.py`
- Create/Modify: `src/bizman/core/context.py`
- Test: `tests/test_core_time.py`

**Interfaces:**
- Produces `UtcClock`, `SystemUtcClock`, `CoreContext`.

- [ ] Write RED tests requiring timezone-aware UTC results and deterministic FixedClock compatibility.
- [ ] Implement `UtcClock(Protocol)` and `SystemUtcClock` using `datetime.now(UTC)`.
- [ ] Implement immutable `CoreContext(assets, data_dir, clock)` with canonical data root.
- [ ] Do not change evidence/CDP timestamp semantics.
- [ ] Run tests and migration golden GREEN.
- [ ] Commit: `feat: add application UTC clock boundary`.

### Task 9: Add stable Core error model

**Files:**
- Create: `src/bizman/core/errors.py`
- Test: `tests/test_core_errors.py`

**Interfaces:**
- Produces `BizManError`, `ConfigurationError`, `AssetError`, `DataIntegrityError`, `ContractMismatchError`, `OperationError`.

- [ ] Write RED contract test for exact exported hierarchy.
- [ ] Implement hierarchy with no transport/infrastructure inheritance.
- [ ] Add narrow exception translations only in actual Core use-case adapters in subsequent task; never blanket-catch `Exception`.
- [ ] Commit: `feat: define core error contract`.

### Task 10: Build application Core use cases

**Files:**
- Create: `src/bizman/core/collection.py`
- Create: `src/bizman/core/detection.py`
- Create: `src/bizman/core/validation.py`
- Modify: `src/bizman/core/__init__.py`
- Test: `tests/test_core_api.py`

**Interfaces:**
- Produces immutable `CollectionRequest`, `CollectionResult`, `DetectionRequest` and public `collect`, `detect_changes`, `validate_repository` APIs.

- [ ] Write RED tests demonstrating use cases accept typed requests/context rather than operational paths.
- [ ] Write RED tests for exact `bizman.core.__all__`.
- [ ] Implement collection Core wrapper around canonical collector implementation.
- [ ] Implement detection Core wrapper around canonical DetectorRunner.
- [ ] Implement repository validation Core wrapper.
- [ ] Translate only expected infrastructure/domain failures into BizMan errors with `raise ... from exc`.
- [ ] Ensure public DTOs are frozen/slotted and serialization-neutral.
- [ ] Run Core and migration golden tests GREEN.
- [ ] Commit: `feat: establish application core API`.

### Task 11: Add unified CLI and preserve legacy commands

**Files:**
- Create: `src/bizman/cli/main.py`
- Create: `src/bizman/cli/collect.py`
- Create: `src/bizman/cli/detect.py`
- Create: `src/bizman/cli/validate.py`
- Modify: `tools/collect_live.py`
- Modify: `tools/detect_changes.py`
- Modify: `tools/validate_repo.py`
- Test: `tests/test_cli_parity.py`

**Interfaces:**
- Produces `bizman collect`, `bizman detect`, `bizman validate`.

- [ ] Write RED tests for new commands, JSON detection output parity, dry-run no-writes, validation exit semantics and collector Ctrl+C=130 contract.
- [ ] Implement CLI parsing/serialization over `bizman.core` only.
- [ ] Convert old scripts to thin delegates to canonical CLI/Core entry points.
- [ ] Verify old/new semantic outputs on deterministic fixtures.
- [ ] Commit: `feat: add unified bizman CLI`.

### Task 12: Enforce dependency architecture and lint

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_public_api_contract.py`

**Interfaces:**
- Produces machine-enforced module dependency rules.

- [ ] Add Import Linter contracts for foundation/sessions/collector/changes/core/cli directions.
- [ ] Add explicit check that `src/bizman` never imports `tools.*`.
- [ ] Configure Ruff target Python 3.11 with an explicit conservative rule set.
- [ ] Run `uv run ruff check src tests tools`.
- [ ] Run Import Linter and fix dependency direction violations without adding indirection solely to silence the tool.
- [ ] Commit: `build: enforce package architecture`.

### Task 13: Verify distribution isolation

**Files:**
- Extend: `tests/test_distribution_contract.py`
- Modify package metadata only if distribution proof exposes a real packaging defect.

**Interfaces:**
- Proves wheel/sdist portability and artifact hygiene.

- [ ] Build with `uv build --no-sources`.
- [ ] Inspect archive entries and fail on operational DB/Parquet/HAR/CAS/.env/browser-profile/tests/tools payloads.
- [ ] Install wheel in a clean temporary environment outside repository checkout.
- [ ] Import public packages and run `bizman --help`.
- [ ] Verify repository assets are explicit external configuration rather than silently duplicated in wheel.
- [ ] Commit: `test: verify isolated package distribution`.

### Task 14: Migrate CI to locked uv with Python compatibility lane

**Files:**
- Modify: `.github/workflows/collector-e2e.yml`
- Modify: `docs/CI.md`

**Interfaces:**
- Produces 3.14 full validation and 3.11 compatibility gates.

- [ ] Add `src/**`, `pyproject.toml`, `uv.lock` workflow triggers.
- [ ] Use pinned `astral-sh/setup-uv` and uv 0.12.10.
- [ ] Replace pip requirements installation with `uv sync --locked`.
- [ ] Primary 3.14 lane: lock check, Ruff, Import Linter, compile, full tests, repository validation, build/isolation smoke.
- [ ] Add lightweight 3.11 compatibility lane for import/compile/unit/CLI smoke.
- [ ] Keep real Chrome E2E on 3.14.
- [ ] Preserve storage benchmark gating and detector benchmark non-gating semantics.
- [ ] Run CI and fix only evidence-backed compatibility/build regressions.
- [ ] Commit: `ci: validate locked package on python 3.11 and 3.14`.

### Task 15: Documentation, deep review and merge gate

**Files:**
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/architecture/*` only where P1 changes current facts.
- Remove: obsolete requirements files only after all consumers use uv.

**Interfaces:**
- Finalizes P1 without starting P2.

- [ ] Rename roadmap stages to stable P1/P2/P3 identifiers and remove stale PR-number coupling.
- [ ] Document installed CLI and compatibility window.
- [ ] Remove old requirements files only when search proves no workflow/document consumer remains.
- [ ] Run complete 24-point P1 exit gate from the design spec.
- [ ] Run independent manual review focused on package duplication, error leakage, clock misuse, arbitrary path exposure, dependency inversion and secret/runtime artifacts.
- [ ] Review CodeRabbit findings; reproduce substantive findings with regression tests before fixes.
- [ ] Verify exact head SHA, base freshness, zero unresolved blocker threads and all required checks success.
- [ ] Squash merge only the verified head.
