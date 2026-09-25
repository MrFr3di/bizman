# AGENTS.md

## Mission
Build a reproducible BizMania research, analytics, and automation system from captured browser evidence. Preserve provenance and never turn an inference into an observed fact.

## Read order
1. `docs/INDEX.md`
2. `knowledge/catalog.json`
3. the local `README.md`/`AGENTS.md` nearest to the files being changed
4. relevant manifest and records under `knowledge/`

## Evidence rules
Use these statuses consistently:
- `observed`: directly present in HAR/HTML/JS/JSON.
- `documented`: stated by captured BizMania Wiki/help.
- `discovered-reference`: referenced by captured HTML/JavaScript but not observed executing.
- `inferred`: reasoned from evidence but not directly stated.
- `hypothesis`: unverified proposed explanation.
- `verified`: deliberately reproduced by an experiment.
- `contradicted`: evidence conflicts with the statement.
- `deprecated`: historical knowledge no longer expected to apply.

Every new protocol or mechanic claim must link to at least one source capture + entry, Wiki topic, or experiment.

## Data rules
- The repository is public; committed content must be safe for public disclosure.
- Raw HAR/CDP streams are evidence, not repository content.
- Never commit cookies, Authorization headers, browser profiles, storage state, passwords, `.env`, SQLite/DuckDB/Parquet operational data, or raw captures.
- Apply `config/redaction-policy.json` before durable normalized runtime events are written.
- Canonical capture/source identifiers use `src.*`; old identifiers are preserved only as explicit aliases.
- Runtime session/event identifiers use UUIDv7; curated knowledge keeps stable `bm.*` identifiers.
- `knowledge/http/application-events/index.json` is the manifest for the sanitized first-party application-event corpus; read only the required `part-*` files.
- Do not silently delete old observations. Add revisions or contradictions.
- Prefer stable IDs and normalized route patterns over copying volatile URLs into prose.
- Keep machine-readable knowledge as the source of truth; Markdown explains it.

## Validation and CI
Before proposing changes to `knowledge/`, `schemas/`, generated indexes, package/Core boundaries or ingestion contracts, run the canonical locked validation surface:

```bash
uv lock --check
uv sync --locked
uv run ruff check src
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run --locked --with coverage==7.16.1 coverage run -m unittest discover -s tests -v
uv run --locked --with coverage==7.16.1 coverage xml
uv run python tools/validate_repo.py
```

`pyproject.toml` plus `uv.lock` are the dependency authority; do not reintroduce pip requirements files. Relevant pull requests use the path-filtered `collector-quality-gate` workflow, which also provides `workflow_dispatch` for manual runs. The gate includes Python 3.14 validation, Python 3.11 compatibility, real Chrome for Testing E2E and benchmarks. Do not add routine `push` or scheduled workflows without a concrete need. See `docs/CI.md`.

## Collector rules
- Collection is passive by default. Keep the CDP command allowlist narrow and do not add mutating browser/game operations to the collector.
- First-party filtering and redaction happen before durable persistence.
- Do not persist WebSocket payloads by default.
- Treat queue overflow, malformed protocol messages and storage failures as explicit failures; never silently drop evidence.
- Runtime operational data belongs under external `BizManData`, never in Git.

## Scope
Observe and document first. Recommendations and write automation must be separate from collection. Do not make destructive or state-changing requests merely to test an endpoint unless explicitly approved.