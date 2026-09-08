# Python Packaging + Application Core Boundary Design

Status: implementation target for P1, based on `main` at `c96d96ecfe0cab7fa4d115c1cf757ab5741f28b2`.

## Goal

Move BizMan production Python code from repository-local `tools/bizman_*` packages into an installable `src/bizman` package and introduce a small, explicit application Core boundary that future CLI, MCP, UI, headless acquisition, current-state projection and recommendation layers can consume without importing implementation modules directly.

P1 is behavior-preserving. It does not add Market State, recommendations, Agent Index, MCP, Current State, analytics, embeddings or write automation.

## Architectural invariants

1. Immutable sanitized evidence remains the source of truth. Databases, indexes and bundles remain rebuildable derivations.
2. No LLM participates in collection normalization, evidence interpretation, change detection, persistence or promotion decisions.
3. Package/import migration must not alter semantic identity. `RuntimeContract`, baseline SHA, `AnalysisProfile` SHA, deterministic `change_id`, detector SQLite schema/application ID/user version and Promotion Bundle canonical bytes must remain unchanged for equivalent fixtures.
4. Existing contract/normalization/extraction/promotion/rule versions are not bumped for an import-path migration.
5. `src/bizman/**/*.py` must never import `tools.*`.
6. `bizman.foundation` is a leaf dependency and must not import collector, sessions, changes, core or CLI.
7. `bizman.sessions` may depend on foundation but must not depend on changes, core or CLI.
8. `bizman.collector` may depend on foundation but must not depend on sessions, changes, core or CLI.
9. `bizman.changes` may depend on foundation and sessions but must not depend on collector, core or CLI.
10. `bizman.core` may orchestrate collector/sessions/changes/foundation but must not depend on CLI or future transport adapters.
11. CLI adapters consume `bizman.core`; business/application logic does not live in CLI parsing or serialization.
12. Public Core application APIs are value-oriented. Arbitrary filesystem paths are not accepted after construction of configuration roots. Only explicit configuration boundaries may accept `repo_root`/`data_dir`.
13. Public Core DTOs are immutable, serialization-neutral stdlib Python values and do not expose `argparse.Namespace`, `sqlite3.Row`, MCP/Pydantic/HTTP/WebSocket implementation types or JSON dictionaries as domain contracts.
14. Expected infrastructure exceptions are translated at the Core boundary into stable BizMan application errors while preserving `__cause__`. Programming errors are not swallowed into generic errors.
15. Read-only application operations must not mutate operational state. Command side effects must be explicit in the use-case contract.
16. Any future list/search/query Core operation must be bounded server-side. `QueryBudget`/cursor types are introduced with the first real list/search consumer in P2 rather than as unused framework code in P1.
17. Future provenance refs use one canonical ref model introduced with Agent Index P2. P1 does not create a competing `SourceRef` representation.
18. Freshness is a policy evaluation, not a stored boolean on every observation. Freshness types are deferred until Current/Market State has a real policy consumer.
19. Future `RunIdentity` is introduced with the first real multi-stage sync/projector run. Collector session IDs are not renamed into generic run IDs in P1.
20. Future `GameClock` is distinct from application UTC wall time.

## Time boundary

P1 introduces only application UTC time injection:

```python
class UtcClock(Protocol):
    def now_utc(self) -> datetime: ...

class SystemUtcClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)
```

`CoreContext` contains the clock:

```python
@dataclass(frozen=True, slots=True)
class CoreContext:
    assets: RepositoryAssets
    data_dir: Path
    clock: UtcClock
```

Rules:

- Core/application-created wall timestamps use the injected `UtcClock`.
- Evidence timestamps, manifest timestamps, CDP timestamps and server-provided timestamps retain their observed values.
- `time.monotonic()` remains valid for durations/timeouts and is not replaced by `UtcClock`.
- `UtcClock` must never be inserted into deterministic hashes/IDs where wall time was not already semantic input.

## Typed repository assets

Repository-managed curated inputs remain outside the wheel in P1 to avoid creating a second copy/source-of-truth.

```python
class AssetId(StrEnum):
    REDACTION_POLICY = "redaction-policy"
    EVENT_SCHEMA = "event-schema"
    SESSION_MANIFEST_SCHEMA = "session-manifest-schema"
    PROMOTION_BUNDLE_SCHEMA = "promotion-bundle-schema"
    KNOWLEDGE_ROOT = "knowledge-root"

@dataclass(frozen=True, slots=True)
class RepositoryAssets:
    root: Path

    def path(self, asset: AssetId) -> Path: ...
```

`RepositoryAssets` accepts only `AssetId`, never arbitrary relative strings. Construction canonicalizes the repository root and verifies containment/required assets fail-closed.

## Core error model

P1 introduces the minimum stable hierarchy:

```python
class BizManError(Exception): ...
class ConfigurationError(BizManError): ...
class AssetError(BizManError): ...
class DataIntegrityError(BizManError): ...
class ContractMismatchError(BizManError): ...
class OperationError(BizManError): ...
```

Core translates only expected infrastructure/domain failures. It must not blanket-catch `Exception` and mask programming errors.

CLI maps `BizManError` to stable non-zero exits/messages. Future MCP maps the same errors to structured tool failures.

## Target package layout

```text
pyproject.toml
uv.lock

src/
  bizman/
    __init__.py

    foundation/
      __init__.py
      fingerprint.py
      redaction.py
      session.py
      validation.py

    sessions/
      __init__.py
      evidence.py
      status.py

    collector/
      __init__.py
      action_script.py
      actions.py
      cdp.py
      correlation.py
      discovery.py
      events.py
      network.py
      runtime.py
      storage.py
      targets.py

    changes/
      __init__.py
      baseline.py
      diff.py
      extract.py
      model.py
      normalization.py
      promotion.py
      runner.py
      state.py
      rules/

    core/
      __init__.py
      assets.py
      context.py
      time.py
      errors.py
      collection.py
      detection.py
      validation.py

    cli/
      __init__.py
      main.py
      collect.py
      detect.py
      validate.py

tools/
  benchmarks/
  ci/
  collect_live.py          # compatibility wrapper only
  detect_changes.py        # compatibility wrapper only
  validate_repo.py         # compatibility wrapper only
  bizman_foundation/       # compatibility re-exports only
  bizman_collector/        # compatibility re-exports only
  bizman_detector/         # compatibility re-exports only
```

`tools/` remains development/CI/migration infrastructure after production migration.

## Package/dependency policy

Python floor remains `>=3.11`; Python 3.14 is the primary full validation lane.

Runtime dependencies stay intentionally small:

- `websockets>=17.1,<18`
- `jsonschema[format]>=4.26,<5`

Development dependencies:

- Ruff 0.16.3
- Import Linter 2.15

Do not add MCP, FastAPI, Pydantic, DuckDB, PyArrow, Polars, SQLAlchemy, Click/Typer, vector databases or orchestration frameworks in P1.

`pyproject.toml` contains compatibility ranges; committed `uv.lock` is the exact tested dependency graph. Build backend is `uv_build>=0.12.10,<0.13` and project tooling is pinned to uv 0.12.10 for P1 reproducibility.

## Public Core surface

Only explicit `bizman.core` exports are supported application API. Internal implementation modules such as `bizman.changes.runner` and `bizman.collector.network` are not public adapter contracts.

Initial Core use cases:

```python
collect(context: CoreContext, request: CollectionRequest) -> CollectionResult
detect_changes(context: CoreContext, request: DetectionRequest) -> DetectorRunSummary
validate_repository(context: CoreContext) -> ValidationResult
```

`CollectionResult` preserves the existing session identity semantics; P1 does not invent a generic `RunIdentity`.

## Compatibility policy

Old import and CLI paths remain temporarily available as thin shims for one downstream architecture stage:

- `tools.bizman_foundation.*`
- `tools.bizman_collector.*`
- `tools.bizman_detector.*`
- `python tools/collect_live.py`
- `python tools/detect_changes.py`
- `python tools/validate_repo.py`

Compatibility modules must re-export canonical objects from `bizman.*`, not duplicate implementations. Identity tests must prove that important classes loaded through old/new namespaces are the same object.

## Pre-migration parity proof

Before moving production code, P1 records deterministic semantic golden fixtures from current `main`.

Detector parity must cover:

- canonical RuntimeContract fingerprint/baseline SHA;
- AnalysisProfile SHA;
- semantic DiffFact set;
- Finding/change ID set;
- DetectorRunSummary semantic output;
- Promotion Bundle canonical bytes.

Collector parity must cover deterministic semantic projection of:

- event kinds;
- normalized HTTP request/response shapes;
- DOM action shapes;
- action↔HTTP correlations;
- redacted structural payload.

Runtime-only values such as UUIDs, wall-clock timestamps or CDP request IDs are normalized out of the golden collector projection where they are not semantic identity.

## Architecture enforcement

Use Import Linter rather than a bespoke AST framework for package dependency contracts. Use Ruff for static/lint checks. Add an explicit public Core export contract test and a source scan ensuring `src/bizman` never imports `tools.*`.

## Distribution verification

CI must verify the installed distribution, not only repository checkout imports:

1. `uv lock --check`
2. `uv sync --locked`
3. `uv build --no-sources`
4. inspect wheel/sdist for forbidden operational artifacts
5. create a clean environment outside the repository checkout
6. install the built wheel
7. import supported package modules
8. run `bizman --help`

The distribution must not contain `BizManData`, SQLite/Parquet/HAR/cookies/browser profiles, `.env*`, CAS payloads, operational promotion bundles, tests or development tools.

## CI policy

Primary Python 3.14 lane:

- locked uv sync
- Ruff
- Import Linter
- compile
- full unit/contract suite
- repository/schema validation
- build/isolation smoke
- real Chrome collector E2E

Compatibility Python 3.11 lane:

- locked sync
- import/compile/unit compatibility suite
- CLI smoke

Benchmarks retain current semantics: storage flush regression remains gating; detector performance reporting remains non-gating.

## P1 exit gate

P1 is mergeable only when all are true:

1. Existing 183 tests plus P1 tests pass.
2. Python 3.14 full lane passes.
3. Python 3.11 compatibility lane passes.
4. `uv lock --check` passes.
5. Ruff passes.
6. Import Linter passes.
7. `uv build --no-sources` passes.
8. Wheel installs/imports outside repository checkout.
9. Real Chrome collector E2E passes.
10. Repository validator passes.
11. Storage benchmark passes.
12. `src/bizman` contains no `tools.*` imports.
13. Dependency direction contracts pass.
14. CLI consumes Core rather than implementation modules directly.
15. Exact supported `bizman.core` exports are contract-tested.
16. Public Core DTOs are immutable and serialization-neutral.
17. Public application APIs do not accept arbitrary filesystem paths.
18. Expected infrastructure exceptions do not leak through Core.
19. Application-generated UTC time uses injected `UtcClock`; source/evidence timestamps are unchanged.
20. RuntimeContract/baseline/Profile/change IDs/Promotion Bundle bytes/SQLite schema remain behavior-equivalent.
21. Pre/post migration semantic golden fixtures match.
22. Compatibility import/CLI shims preserve object identity and behavior.
23. No new sensitive/runtime artifacts are packaged or committed.
24. CodeRabbit/manual review has no unresolved blocker.

## Explicitly deferred

P1 does not implement `SourceRef`, `ObservationMeta`, freshness policy/results, `GameClock`, generic `RunIdentity`, pagination/query budget, capability discovery, Market/Analytics services, State/History stores, recommendation DTOs, MCP or Agent Index. Their architectural seams are reserved without speculative interfaces.