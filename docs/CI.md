# CI policy

The repository is public. GitHub-hosted CI is used as a pull-request quality gate where it materially improves confidence, especially for deterministic package/Core contracts, detector integration and real Chrome/CDP regression tests.

## Default triggers

- `pull_request` for relevant package, tool, schema, test, workflow, documentation and knowledge changes.
- `workflow_dispatch` for explicit re-runs and diagnostics.
- No routine `push` workflow.
- No scheduled workflow unless a future monitoring use case justifies it.

The environment is reproducible from `pyproject.toml` plus the committed `uv.lock`. CI uses `uv 0.12.10`; the project requires Python >=3.11, with Python 3.14 as the primary/full lane.

## Quality gate

The `collector-quality-gate` workflow currently performs four jobs.

### 1. `validate` — primary gating lane, Python 3.14

The full lane runs:

1. `uv lock --check` and `uv sync --locked`.
2. Ruff against the installable `src/bizman` package.
3. Import Linter contracts for the package dependency directions.
4. `python -m compileall -q src tools tests`.
5. Full `unittest` discovery.
6. `tools/validate_repo.py` for committed structured knowledge and schemas.

The package architecture gate enforces these dependency directions:

- `foundation` is a dependency leaf;
- `sessions` does not depend on higher layers;
- `collector` is independent from detector/application layers;
- `changes` does not depend on collector/application layers;
- `core` does not depend on CLI;
- CLI directly consumes Core rather than lower implementation packages.

A source scan also prevents the installable `src/bizman` package from importing the legacy `tools.*` namespace.

The full test suite covers, among other invariants:

- exact public Core exports, typed repository assets and injected UTC clock;
- frozen/slotted, path-free Core request/result DTOs;
- unified CLI parity with the supported legacy entry points;
- package migration semantic fingerprint equivalence;
- deterministic RuntimeContract/baseline/profile identity;
- Draft 2020-12 manifest/event/Promotion Bundle contracts;
- traversal and symlink-escape rejection for evidence roots;
- actual JSONL/CAS byte hashing and corruption fail-closed behavior;
- `UNKNOWN != EMPTY` body semantics;
- semantic diff separated from stable/versioned rules;
- SQLite STRICT/WAL compatibility, `BEGIN IMMEDIATE`, checkpoints and change identity;
- transactional outbox rollback/recovery;
- value-free canonical Promotion Bundles and privacy rejection;
- synthetic filesystem -> evidence -> extraction -> diff -> rules -> SQLite/outbox -> bundle E2E;
- rerun/idempotence and downstream synthetic-secret byte scans.

### Distribution isolation in the full lane

`tests/test_distribution_contract.py` builds both wheel and sdist with `uv build --no-sources`, scans their archive entries, and installs the wheel into a fresh temporary virtual environment outside the repository checkout.

The proof requires:

- exactly one wheel and one sdist;
- no tests/tools payload, operational DB/Parquet/HAR files, `.env`, CAS or browser-profile payloads in the distributions;
- repository `config/`, `schemas/` and `knowledge/` assets are not silently duplicated into the wheel;
- public `bizman` packages import from the installed wheel rather than the checkout;
- the installed `bizman --help` console entry point works and exposes `collect`, `detect` and `validate`.

Repository assets remain explicit external configuration through `RepositoryAssets`; packaging does not turn them into hidden package data.

### 2. `compatibility` — gating Python 3.11 floor

Python 3.11 is intentionally a lightweight compatibility lane rather than a duplicate of the full Chrome/benchmark workload. It runs:

- locked `uv sync`;
- compile of the installable `src` package;
- Core API/use-case contract tests;
- package-migration semantic contract;
- unified CLI parity tests;
- imports of all current public package layers;
- `bizman --help` smoke.

The heavy jobs depend on both `validate` and `compatibility`, so a Python-floor regression fails early and avoids unnecessary Chrome/benchmark execution.

### 3. `collector-e2e` — gating

After both correctness lanes pass, a real Chrome for Testing end-to-end CDP run executes against a local fixture server. The fixture exercises first-party HTTP/WebSocket capture plus a real DOM form submit and verifies the resulting immutable action-to-HTTP correlation.

The E2E fixture deliberately uses only loopback services and synthetic secrets. Its form contains both a safe field and a synthetic secret field; the verifier requires the DOM action metadata to retain only the safe field name, the persisted URL-encoded request body to retain only the safe value, and every synthetic secret value to be absent from normalized events and all persisted CAS artifacts. It does not connect to BizMania, export browser state, or perform game writes.

The collector is terminated by SIGINT after the bounded E2E observation window, so `cancelled` is an accepted and schema-valid terminal session status for this test. The verifier still requires contiguous event sequencing and complete evidence for the expected fixture traffic before accepting the run.

### 4. `benchmark` — storage gating, detector performance non-gating

The benchmark job publishes two families of measurements:

- the existing A/B/C collector storage flush benchmark;
- the Change Detector benchmark from `tools/benchmarks/detector_stream.py`.

The detector benchmark uses at least 100,000 deterministic synthetic events by default and compares:

- A — linear scan of all compiled route patterns;
- B — current exact-dict + template bucket-by-segment-count `PathMatcher`;
- C — simple segment trie.

A/B/C matcher outputs must be semantically equivalent before timing is reported. The benchmark records median requests/s, then runs the real validated `EvidenceReader + ObservationExtractor` path and reports source events/s, effective validation events/s and peak `tracemalloc` memory. Integrity checks are never disabled for benchmark speed.

Detector performance is intentionally `continue-on-error`/non-gating initially because shared GitHub runners are noisy. Correctness remains gating through the unit/integration suite; benchmark results are evidence for future optimization, not a reason to weaken validation.

## Local commands

Install/sync the exact committed environment and run the same primary deterministic validation surface with:

```bash
uv lock --check
uv sync --locked
uv run ruff check src
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run python -m unittest discover -s tests -v
uv run python tools/validate_repo.py
```

Canonical application commands use the installed unified CLI:

```bash
uv run bizman collect --endpoint http://127.0.0.1:9222 --data-dir "$HOME/BizManData"
uv run bizman detect --data-dir "$HOME/BizManData"
uv run bizman detect --data-dir "$HOME/BizManData" --dry-run
uv run bizman validate
```

The existing `tools/collect_live.py`, `tools/detect_changes.py` and `tools/validate_repo.py` commands remain thin compatibility delegates during the migration window.

Detector state and bundles remain local/rebuildable under `BizManData` and are never CI artifacts intended for commit.

## Reproducibility

GitHub Actions are SHA-pinned. Package builds use the standards-oriented `uv build --no-sources` path so local source overrides cannot silently make a publishable build succeed. Chrome for Testing is downloaded from the official Google Chrome for Testing manifest for the current Stable channel, and the exact runtime CDP protocol is still discovered and fingerprinted by the collector itself.

Detector interpretation is separately replay-scoped through `analysis_profile_sha256`, which includes baseline/redaction and versioned normalization/extraction/rule semantics. Packaging, CI or performance changes therefore do not replace the semantic/profile identity required for reproducible detector outputs.
