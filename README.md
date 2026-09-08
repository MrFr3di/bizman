# BizMan

BizMan is a deterministic evidence/state platform for BizMania. It combines a structured public knowledge base with a privacy-safe passive Chrome/CDP collector, deterministic action-to-HTTP correlation, a replayable Change Detector, value-free Promotion Bundles, and a small installable application Core/CLI boundary.

The central invariant is: **immutable sanitized evidence is the source of truth; databases and indexes are rebuildable derivations; no LLM decides what was observed.**

Raw HAR captures, browser/session state and operational databases remain private and external. Committed source manifests retain SHA-256/provenance so derived observations can be traced back to supplied captures without publishing sensitive runtime material.

## Current stage

Completed foundations include:

- structured corpus/provenance/schema validation;
- passive version-aware Chrome/CDP collection;
- privacy-safe DOM action context and deterministic action-to-HTTP correlation;
- deterministic Change Detector with versioned rules, analysis profiles, SQLite checkpoints/outbox and Promotion Bundles;
- installable `src/bizman` package and stable `bizman.core` application boundary;
- unified `bizman` CLI;
- locked `uv` environment, Python 3.14 full validation and Python 3.11 compatibility validation.

Current delivery stage is **P1 — Python package + Core boundary**. The next stage is **P2 — Agent Index + Session Intelligence**, followed by **P3 — read-only MCP**. See `docs/ROADMAP.md`.

## Current corpus

Derived from 3 supplied HAR captures:

- 17,108 total network entries
- 16,202 first-party BizMania entries
- 555 significant first-party application events
- 17 observed POST requests
- 68 network endpoint signatures
- 761 internal HTML/JavaScript route patterns
- 87 unique HTML form signatures
- 74 observed form parameters
- 36 unique JSON responses
- 180 normalized non-Wiki game HTML pages
- 914 static-resource census entries
- 29 unique JavaScript resources
- 14 protocol-relevant JavaScript snippets
- 87 captured Wiki topics / 89 Wiki navigation topics
- 303 products
- 19 observed domain entities
- 11 normalized state-changing action families

## Start here

1. `knowledge/catalog.json` — machine-readable master catalog and authoritative dataset counts.
2. `docs/INDEX.md` — human navigation.
3. `docs/ROADMAP.md` — stable delivery stages and architecture direction.
4. `AGENTS.md` — rules for Codex/other agents.
5. `knowledge/http/operation-index.json` — high-value protocol/action index.
6. `knowledge/actions/catalog.json` — observed write actions.

## Layout

```text
pyproject.toml                  package/dependency/tooling contract
uv.lock                         exact reproducible dependency lock
src/bizman/
  foundation/                   deterministic shared primitives
  sessions/                     immutable evidence/session boundary
  collector/                    passive CDP collector
  changes/                      detector/diff/rules/state/promotion
  core/                         stable application use-case boundary
  cli/                          thin command-line adapter over Core
config/                         capture/runtime policies
knowledge/
  sources/                      capture provenance and hashes
  actions/                      normalized observed write actions
  domain/                       products and observed game entities
  forms/                        cross-action parameter index
  http/                         events/endpoints/routes/forms/JSON/assets
  javascript/                   script hashes and relevant snippets
  pages/                        normalized game pages
  wiki/topics/                  captured Wiki topic texts
schemas/                        structured-data contracts
tests/                          deterministic unit/contract/integration tests
docs/                           architecture, CI, provenance and plans
tools/                          compatibility delegates, CI helpers, benchmarks
```

`tools/` is no longer the canonical production namespace. Production implementations live under `src/bizman`; supported legacy scripts remain thin compatibility delegates during P1.

Large corpora are partitioned behind `index.json` manifests. Each manifest records counts, part names and offsets where applicable so tooling can read the required slice rather than loading an entire corpus.

## Evidence and confidence

Knowledge distinguishes:

- `observed` — directly present in captured traffic/HTML/JavaScript;
- `documented` — stated by the captured BizMania Wiki;
- `discovered-reference` — referenced by captured HTML/JavaScript but not observed executing;
- `inferred` — deterministically derived from observations;
- `hypothesis` — not yet experimentally verified;
- `verified` — reproduced/confirmed;
- `contradicted` / `deprecated` — retained for history when applicable.

Canonical source IDs use the `src.*` namespace. Unknown evidence is never silently treated as empty/known evidence.

## Install and use

Python 3.14 is the preferred development/runtime version. Python 3.11 is the supported compatibility floor.

```bash
uv sync --locked
```

Canonical CLI commands require an explicit repository asset root:

```bash
uv run bizman validate --repo-root "$PWD"

uv run bizman collect \
  --repo-root "$PWD" \
  --endpoint http://127.0.0.1:9222 \
  --data-dir "$HOME/BizManData"

uv run bizman detect --repo-root "$PWD" --data-dir "$HOME/BizManData"
uv run bizman detect --repo-root "$PWD" --data-dir "$HOME/BizManData" --dry-run
```

`--repo-root` is an explicit configuration boundary for curated repository assets; operational use-case DTOs do not accept arbitrary filesystem paths.

The collector should run against a dedicated Chrome profile exposing a local DevTools endpoint. Collection is passive: the CDP command allowlist permits observation/instrumentation required for capture but not game writes.

Legacy commands `tools/collect_live.py`, `tools/detect_changes.py` and `tools/validate_repo.py` remain available as compatibility delegates in P1. New integrations should use the installed `bizman` CLI or `bizman.core` rather than importing `tools.*`.

Operational sessions/events/CAS, browser profiles, detector SQLite state and Promotion Bundles stay under the external `BizManData` root and are never package assets or intended Git content.

## Application Core boundary

`bizman.core` is the supported adapter boundary for application use cases. It currently exposes typed repository assets/configuration, UTC clock injection, stable Core errors, immutable request/result DTOs, and three use cases:

- collection;
- change detection;
- repository validation.

CLI code consumes Core rather than lower implementation packages. Future MCP adapters must follow the same rule; P1 intentionally does not add MCP, Agent Index, Current State, analytics or write automation.

## Change detection

The detector replays finalized sanitized evidence through a versioned semantic pipeline:

```text
EvidenceReader
  -> Observation extraction
  -> SemanticDiff
  -> stable/versioned RuleEngine
  -> SQLite checkpoint/change state + transactional outbox
  -> schema-valid value-free Promotion Bundle
```

Important safety properties include cryptographic evidence/CAS verification, `UNKNOWN != EMPTY`, fail-closed corruption/path checks, stable change identities, explicit reason/provenance/versioning, rerun idempotence, and privacy scanning of derived outputs.

## Security

This repository is public. Never commit raw HAR/CDP captures, cookies, authorization/session data, browser profiles, `.env` files, operational SQLite/DuckDB databases, Parquet files or private runtime state. See `SECURITY.md`, `.gitignore` and `config/redaction-policy.json`.

## Validation and CI

Run the primary local quality surface with:

```bash
uv lock --check
uv sync --locked
uv run ruff check src
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run python -m unittest discover -s tests -v
uv run python tools/validate_repo.py
```

Relevant pull requests run:

- Python 3.14 full locked validation;
- Python 3.11 compatibility/import/Core/CLI smoke;
- machine-enforced package dependency contracts;
- wheel/sdist build and isolated wheel-install proof;
- real Chrome for Testing CDP E2E against loopback fixtures;
- storage benchmark plus non-gating detector performance evidence.

See `docs/CI.md` for the exact policy.

## Delivery direction

The stable sequence is:

```text
D1  deterministic Change Detector        completed
P1  Python package + Core boundary       current
P2  Agent Index + Session Intelligence   next
P3  read-only MCP
P4  replayable Current State
P5  Parquet history + deterministic analytics
P6  experiment framework
P7  agent/retrieval evaluation and optimization
P8  recommendation layer
P9+ guarded write automation
```

Write automation remains deliberately last and physically separated from the read/evidence stack.
