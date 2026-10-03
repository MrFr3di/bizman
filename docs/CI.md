# CI policy

The repository is public. GitHub-hosted CI is used as a pull-request quality gate where it materially improves confidence, especially for deterministic package/Core contracts, detector integration and real Chrome/CDP regression tests.

## Default triggers

- `pull_request` for relevant package, tool, schema, test, workflow, documentation and knowledge changes.
- `workflow_dispatch` for explicit re-runs and diagnostics.
- No routine `push` workflow.
- No scheduled workflow unless a future monitoring use case justifies it.

The environment is reproducible from `pyproject.toml` plus the committed `uv.lock`. CI uses `uv 0.12.22`; the project requires Python >=3.11, with Python 3.14 as the primary/full lane.

## Quality gate

The `collector-quality-gate` workflow currently performs four jobs.

### 1. `validate` — primary gating lane, Python 3.14

The full lane runs:

1. `uv lock --check` and `uv sync --locked --extra telegram`; the `telegram` extra is synced in this lane so the telegram adapter tests and imports run against aiogram instead of being skipped.
2. Ruff against the whole repository, including `tests/` and `tools/`.
3. Import Linter contracts for the package dependency directions.
4. `python -m compileall -q src tools tests`.
5. Full `unittest` discovery under exact-version-pinned Coverage.py `7.16.1`.
6. Generate `coverage.xml` for the installable `bizman` package and upload it as a short-lived CI artifact.
7. `tools/validate_repo.py` for committed structured knowledge and schemas.
8. Gating P3 MCP completion evaluation through the official SDK, including the installed stdio entry point, with a machine-readable `mcp-p3-evaluation` artifact.
9. When explicitly activated, run CI-based SonarQube Cloud analysis and import that exact coverage report.

The package architecture gate enforces these dependency directions:

- `foundation` is a dependency leaf;
- `sessions` does not depend on higher layers;
- `collector` is independent from detector/read-model/application layers;
- `changes` does not depend on collector/read-model/application layers;
- `readmodel` may consume deterministic lower layers but not collector/Core/CLI;
- `current` may consume deterministic foundation/session/change semantics but not collector/readmodel/Core/adapters;
- `market` depends only on foundation and cannot import sessions/collector/changes/readmodel/current/experiments/ingest/Core/CLI/adapters;
- `experiments` depends only on foundation and cannot import sessions/collector/changes/readmodel/current/market/ingest/Core/CLI/adapters;
- `ingest` depends only on foundation and cannot import sessions/collector/changes/readmodel/current/market/experiments/Core/CLI/adapters;
- `core` does not depend on CLI or MCP adapters;
- CLI directly consumes Core rather than lower implementation packages, including `readmodel`;
- MCP directly consumes Core and cannot import foundation/sessions/collector/changes/readmodel/CLI implementation layers;
- Telegram directly consumes Core and cannot import foundation/sessions/collector/changes/readmodel/current/CLI/MCP implementation layers.

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
- rerun/idempotence and downstream synthetic-secret byte scans;
- deterministic curated read-model projection, SQLite identity/rebuild and bounded FTS retrieval;
- 590-record curated read-model coverage across actions/products/entities/endpoints/operations/forms/Wiki;
- versioned retrieval evals with Recall@1/5, MRR and evidence correctness;
- regression proof that expanded corpora do not reduce the earlier P2-A retrieval metrics;
- exact 10-tool MCP surface, explicit bounded protocol schemas and result budgets;
- P2-E retrieval v1/v2/v3 replay through MCP with no metric regression;
- all current action knowledge records resolve and trace in <=2 MCP calls;
- common evidence/session task call budgets;
- sanitized MCP failures and real installed-stdio protocol cleanliness;
- Current State SQLite identity/schema hardening, atomic replacement and fingerprint verification;
- deterministic finalized-evidence replay, event-count/last-sequence checkpoints and delete/replay equivalence;
- P4-B bounded/sanitized company-roster response evidence through passive `Network.getResponseBody`;
- deterministic companies/units projection with latest-positive-observation semantics and UNKNOWN != deletion;
- company/unit provenance, foreign keys, state-fingerprint tamper detection, parser-drift stale semantics and crash-safe P4-A schema-v1 → P4-B schema-v2 replacement;
- P4-C typed shop/goods response capture, canonical unit-economics artifacts, catalog-semantic fingerprinting, unit-product provenance and explicit per-unit `ready/unknown/stale` goods coverage;
- exact v1/v2 → v3 Current State staged replacement, product-row tamper/integrity checks and deterministic product delete/replay equivalence;
- path-free Core Current State rebuild and stable error translation;
- Telegram adapter commands over Core with sanitized errors, bounded plain-text replies, strict chat-allowlist authorization and fail-closed CLI startup.

### Python coverage and SonarQube Cloud

The Python 3.14 lane measures the same full `unittest` suite; coverage instrumentation does not replace, filter or split the tests. Coverage.py is pinned to `7.16.1` and layered onto the locked project environment with `uv run --locked --with`, so `pyproject.toml` + `uv.lock` remain the dependency authority for BizMan itself.

Coverage configuration lives in `pyproject.toml`. It records branch coverage for the installable `bizman` package with relative paths, writes `coverage.xml`, and uploads the XML as the `python-coverage` workflow artifact.

SonarQube Cloud must use CI-based analysis before it can consume external Python coverage. The current Automatic Analysis mode cannot import `coverage.xml`. Migration is deliberately explicit:

1. disable Automatic Analysis for the SonarQube Cloud project;
2. add repository secret `SONAR_TOKEN`;
3. add repository variable `SONAR_ORGANIZATION` containing the actual SonarQube Cloud organization key;
4. set repository variable `SONAR_CI_ENABLED=true`.

When enabled for same-repository pull requests or manual runs, the workflow fails closed if the organization key or token is unavailable, then runs the SHA-pinned SonarQube scanner. Fork pull requests still run and publish coverage but skip the secret-bearing scanner step.

`sonar-project.properties` is the CI scanner contract and points Python coverage at `coverage.xml`. `.sonarcloud.properties` remains only as the compatibility scope for Automatic Analysis until that external mode is disabled.

### Distribution isolation in the full lane

`tests/test_distribution_contract.py` builds both wheel and sdist with `uv build --no-sources`, scans their archive entries, and installs the wheel into a fresh temporary virtual environment outside the repository checkout.

The proof requires:

- exactly one wheel and one sdist;
- no tests/tools payload, operational DB/Parquet/HAR files, `.env`, CAS or browser-profile payloads in the distributions;
- repository `config/`, `schemas` and `knowledge` assets are not silently duplicated into the wheel;
- public `bizman` packages, including `bizman.current` and `bizman.mcp`, import from the installed wheel rather than the checkout;
- the installed `bizman --help` console entry point works and exposes `collect`, `detect` and `validate`;
- the installed `bizman-mcp --help` entry point is present and the protocol contract is exercised separately by the MCP tests/evaluator;
- the installed `bizman-telegram --help` entry point is present; its runtime loop is never exercised in tests;
- the installed `bizman-ingest --help` entry point is present and exposes `--har` and `--out`;
- the plain wheel install stays aiogram-free by default: `import bizman.telegram` and `bizman-telegram --help` work without the `telegram` extra, and starting the polling loop fails closed with exit code 2 until `bizman[telegram]` is installed.

Repository assets remain explicit external configuration through `RepositoryAssets`; packaging does not turn them into hidden package data.

### 2. `compatibility` — gating Python 3.11 floor

Python 3.11 is intentionally a lightweight compatibility lane rather than a duplicate of the full Chrome/benchmark workload. It runs:

- locked `uv sync --extra telegram`;
- compile of the installable `src` package;
- Core API/use-case contract tests;
- Current State replay/store/company-unit contract tests;
- P4-B response-evidence compatibility smoke;
- MCP API/evaluation contract tests;
- package-migration semantic contract;
- unified CLI parity tests;
- imports of all current public package layers, including `bizman.telegram.bot` with the `telegram` extra;
- `bizman --help`, `bizman-mcp --help` and `bizman-telegram --help` smoke.

The heavy jobs depend on both `validate` and `compatibility`, so a Python-floor regression fails early and avoids unnecessary Chrome/benchmark execution.

### P3 MCP completion evaluation — gating inside `validate`

`tools/evaluations/mcp_p3.py` is a correctness/protocol gate, not a timing benchmark. It builds the deterministic 590-record Agent Index plus a 20-session/40-change runtime fixture and evaluates the actual MCP surface through the official SDK.

It requires:

- exactly 14 read-only/closed-world tools;
- explicit input/output schemas with bounded output collections;
- no arbitrary path/SQL/database tool parameters;
- v1/v2/v3 retrieval metrics no lower than the P2-E baseline;
- all 11 current action records resolve and trace in <=2 tool calls;
- common evidence/session median call count <=3;
- compact/default structured output <=8 KiB;
- representative ordinary structured output <=16 KiB;
- no silent list truncation;
- sanitized invalid-input/missing-index errors;
- a successful initialize/list/call/close exchange through the installed `bizman-mcp` stdio process.

The evaluator writes `mcp-p3-evaluation.json`, the workflow uploads it as the short-lived `mcp-p3-evaluation` artifact and appends a concise Actions summary. Any failed acceptance condition exits non-zero and fails `validate`.

The durable reference result is `docs/benchmarks/p3e-mcp-baseline-2026-09-29.md`.

### 3. `collector-e2e` — gating

After both correctness lanes pass, a real Chrome for Testing end-to-end CDP run executes against a local fixture server. The fixture exercises first-party HTTP/WebSocket capture, a real DOM form submit, the P4-B company roster response path and the P4-C typed shop/goods response path.

The E2E fixture deliberately uses only loopback services and synthetic secrets. Its form contains both a safe field and a synthetic secret field; the verifier requires the DOM action metadata to retain only the safe field name and the persisted URL-encoded request body to retain only the safe value. The goods page uses the frozen C0 row-bound `product=N` structure with two products and secret label/attribute/hidden-input canaries; the verifier requires only canonical `UnitEconomicsV1` values to reach CAS, then checks unit-product provenance, explicit `ready/unknown` goods coverage and delete/replay fingerprint equality. Every synthetic secret value must be absent from normalized events and all persisted CAS artifacts. The loopback fixture may use plain HTTP solely because the collector admits insecure goods capture only for loopback origins; remote BizMania capture remains HTTPS-only. The E2E does not connect to BizMania, export browser state, or perform game writes.

The collector is terminated by SIGINT after the bounded E2E observation window, so `cancelled` is an accepted and schema-valid terminal session status for this test. The verifier still requires contiguous event sequencing and complete evidence for the expected fixture traffic before accepting the run.

### 4. `benchmark` — storage gating, detector/read-model performance non-gating

The benchmark job publishes three families of measurements:

- the existing A/B/C collector storage flush benchmark;
- the Agent Index/Core benchmark from `tools/benchmarks/readmodel_core.py`;
- the Change Detector benchmark from `tools/benchmarks/detector_stream.py`.

The P2-E Agent Index/Core benchmark reports:

- v1/v2/v3 retrieval metrics by corpus/category, including negative/no-match accuracy;
- fresh Core-call latency versus warm `KnowledgeIndex` latency (p50/p95/min/max);
- explicit fresh-open integrity-validation cost;
- deterministic rebuild time, SQLite bytes and peak `tracemalloc` memory for curated-only plus small/large synthetic runtime projections;
- representative serialized Core result sizes;
- `EXPLAIN QUERY PLAN` details for exact knowledge/session/change reads, keyset pages and FTS.

Its JSON output is uploaded as the short-lived `readmodel-benchmark` artifact and a concise table is appended to the Actions summary. Shared-runner timing remains `continue-on-error`/non-gating; retrieval correctness itself is gating through the v1/v2/v3 unit evaluation corpus.

The detector benchmark uses at least 100,000 deterministic synthetic events by default and compares:

- A — linear scan of all compiled route patterns;
- B — current exact-dict + template bucket-by-segment-count `PathMatcher`;
- C — simple segment trie.

A/B/C matcher outputs must be semantically equivalent before timing is reported. The benchmark records median requests/s, then runs the real validated `EvidenceReader + ObservationExtractor` path and reports source events/s, effective validation events/s and peak `tracemalloc` memory. Integrity checks are never disabled for benchmark speed.

Detector and Agent Index/Core performance are intentionally `continue-on-error`/non-gating initially because shared GitHub runners are noisy. Correctness, deterministic generation/integrity and retrieval evaluation remain gating through the unit/integration suite; benchmark results are evidence for future optimization, not a reason to weaken validation.

## Local commands

Install/sync the exact committed environment and run the same primary deterministic validation surface with:

```bash
uv lock --check
uv sync --locked --extra telegram
uv run ruff check
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run --locked --extra telegram --with coverage==7.16.1 coverage run -m unittest discover -s tests -v
uv run --locked --extra telegram --with coverage==7.16.1 coverage xml
uv run python tools/validate_repo.py
uv run python tools/evaluations/mcp_p3.py
```

Canonical application commands use the installed unified CLI and an explicit repository asset root:

```bash
uv run bizman collect \
  --repo-root "$PWD" \
  --endpoint http://127.0.0.1:9222 \
  --data-dir "$HOME/BizManData"
uv run bizman detect --repo-root "$PWD" --data-dir "$HOME/BizManData"
uv run bizman detect --repo-root "$PWD" --data-dir "$HOME/BizManData" --dry-run
uv run bizman validate --repo-root "$PWD"
uv run bizman-mcp --repo-root "$PWD" --data-dir "$HOME/BizManData"
```

The explicit `--repo-root` is the configuration boundary for curated repository assets. Core operation DTOs remain path-free.

The existing `tools/collect_live.py`, `tools/detect_changes.py` and `tools/validate_repo.py` commands remain thin compatibility delegates during the migration window.

Detector state and bundles remain local/rebuildable under `BizManData` and are never CI artifacts intended for commit.

## Reproducibility

GitHub Actions are SHA-pinned. Package builds use the standards-oriented `uv build --no-sources` path so local source overrides cannot silently make a publishable build succeed. Chrome for Testing is downloaded from the official Google Chrome for Testing manifest for the current Stable channel, and the exact runtime CDP protocol is still discovered and fingerprinted by the collector itself.

Detector interpretation is separately replay-scoped through `analysis_profile_sha256`, which includes baseline/redaction and versioned normalization/extraction/rule semantics. Packaging, CI or performance changes therefore do not replace the semantic/profile identity required for reproducible detector outputs.