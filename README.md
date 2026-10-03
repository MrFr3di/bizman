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
- deterministic `bizman.readmodel` Agent Index over 632 curated records plus verified session/change intelligence;
- bounded path-free `bizman.core` read API for knowledge, provenance, sessions and profile-scoped changes;
- read-only `bizman-mcp` stdio adapter with 14 bounded Core-backed tools;
- read-only `bizman-telegram` long-polling adapter with 16 bounded Core-backed commands;
- deterministic `bizman.current` replay foundation over immutable evidence;
- unified `bizman` CLI;
- locked `uv` environment, Python 3.14 full validation and Python 3.11 compatibility validation.

**P3 — read-only MCP v1 is complete.** P4 is now active. **P4-A — Current State storage, identity and replay foundation is complete. P4-B — companies/units projection is complete. P4-C — the C0 product-identity evidence gate passed on 2026-10-03 and Current State unit-economics is implemented under the authorized `UnitEconomicsV1` contract; C3 hardening advances the derived store to schema/projection v4 solely to preserve explicit orphan goods provenance.** The collector stores a typed goods artifact rather than raw HTML, Current State projects unit/product rows with catalog-semantic fingerprinting, and every verified unit carries explicit `shop.goods` coverage (`ready`/`unknown`/`stale`). Real Chrome CI exercises the same goods capture/parser path with privacy canaries. See `docs/research/p4c-product-evidence.md`, `docs/ROADMAP.md` and `docs/benchmarks/p3e-mcp-baseline-2026-09-29.md`.

## Current corpus

Derived from 4 supplied HAR captures (plus one external offline webcopy snapshot used only for documented help pages and domain reference data):

- 17,204 total network entries
- 16,294 first-party BizMania entries
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
- 89 recovered Wiki topic revisions from the 2026-10-03 read-only walk
- 51 endpoint observations, 94 surface-probe outcomes and 7 asset/query-key records from the 2026-10-03 walk
- 303 products plus 301 documented product attribute/recipe records from the help-page snapshot
- 18 recovered product categories covering all 303 curated products
- 71 buildings: 48 Wiki-stated prices plus numeric construction ids and required materials
- 49 enterprise types, 34 help/announcement records, 4 legal records and 7 official currency rates
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
  current/                      replayable Current State identity/store/replay
  readmodel/                    rebuildable Agent Index + runtime intelligence
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

`tools/` is no longer the canonical production namespace. Production implementations live under `src/bizman`; supported legacy scripts remain thin compatibility delegates during the compatibility window.

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

uv run bizman-mcp \
  --repo-root "$PWD" \
  --data-dir "$HOME/BizManData"
```

`--repo-root` is an explicit configuration boundary for curated repository assets; operational use-case DTOs do not accept arbitrary filesystem paths.

The collector should run against a dedicated Chrome profile exposing a local DevTools endpoint. Collection is passive: the CDP command allowlist permits observation/instrumentation required for capture but not game writes.

Legacy commands `tools/collect_live.py`, `tools/detect_changes.py` and `tools/validate_repo.py` remain available as compatibility delegates during the compatibility window. New integrations should use the installed `bizman` CLI or `bizman.core` rather than importing `tools.*`.

## Telegram adapter

`bizman-telegram` runs the same Core read surface as the MCP adapter behind a personal Telegram bot over long polling. It is read-only, replies in plain text and enforces strict authorization:

```bash
BIZMAN_TELEGRAM_BOT_TOKEN=<token> \
BIZMAN_TELEGRAM_CHAT_IDS=<chat_id>[,<chat_id>...] \
uv run bizman-telegram \
  --repo-root "$PWD" \
  --data-dir "$HOME/BizManData"
```

Both environment variables are required; startup fails closed (exit 2) without them. The bot answers only allowlisted chat ids, silently ignores everyone else, and exposes 16 bounded commands: `/help`, `/status`, `/sessions`, `/session`, `/compare`, `/anomalies`, `/changes`, `/change`, `/k`, `/kb`, `/trace`, `/current`, `/units`, `/unit`, `/products`, `/plan`. Each command performs at most one Core read use case and never exposes filesystem paths, tokens or raw evidence bytes. The first page of every list is shown; opaque Core cursors are not surfaced. The Current State commands report explicit UNKNOWN and stale/unknown-surface markers instead of guessing.

Operational sessions/events/CAS, browser profiles, detector SQLite state, Current State SQLite and Promotion Bundles stay under the external `BizManData` root and are never package assets or intended Git content.

## Application Core boundary

`bizman.core` is the supported adapter boundary for application use cases. It exposes typed repository assets/configuration, UTC clock injection, stable Core errors, immutable request/result DTOs, and bounded use cases for:

- collection;
- change detection;
- repository validation;
- knowledge resolve/search/get;
- evidence provenance trace;
- session list/get, deterministic comparison and anomaly-signal listing;
- profile-scoped change list/get;
- deterministic Current State rebuild and bounded Current State status/company/unit/product reads.

CLI and MCP adapters consume Core rather than lower implementation packages. The derived Agent Index lives below Core in `bizman.readmodel`; callers never provide a database path, and list cursors are bounded, operation-scoped and bound to the semantic index generation. Current State similarly derives its fixed database path internally as `BizManData/state/current.sqlite3`; callers do not provide a database path or arbitrary analysis-profile hash, and Current State list cursors are scoped to the request filter and bound to the projection `state_fingerprint`. The `bizman.telegram` adapter follows the same rule and consumes only Core.

The completed P3 MCP adapter is read-only, local-stdio only and now exposes exactly 14 bounded tools. It does not expose arbitrary paths/SQL, raw HAR/session bytes, HTTP/OAuth, resources, Tasks or write automation. P4 adds a read-only Current State surface over the same Core: `bizman current rebuild|status|companies|units|products` on the CLI and `current.status`, `current.companies`, `current.units`, `current.products` on MCP. Analytics and writes remain later stages.

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
uv run ruff check
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run --locked --with coverage==7.16.1 coverage run -m unittest discover -s tests -v
uv run --locked --with coverage==7.16.1 coverage xml
uv run python tools/validate_repo.py
uv run python tools/evaluations/mcp_p3.py
```

Relevant pull requests run:

- Python 3.14 full locked validation;
- Python 3.11 compatibility/import/Core/CLI smoke;
- machine-enforced package dependency contracts;
- wheel/sdist build and isolated wheel-install proof;
- real Chrome for Testing CDP E2E against loopback fixtures;
- gating P3 MCP protocol/completion evaluation with a machine-readable artifact;
- storage benchmark plus non-gating Agent Index/Core and detector performance evidence.

See `docs/CI.md` for the exact policy.

## Delivery direction

The stable sequence is:

```text
D1  deterministic Change Detector        completed
P1  Python package + Core boundary       completed
P2  Agent Index + Session Intelligence   completed
P3  read-only MCP                        complete
P4  replayable Current State              current
P5  Parquet history + deterministic analytics
P6  experiment framework
P7  agent/retrieval evaluation and optimization
P8  recommendation layer
P9+ guarded write automation
```

Write automation remains deliberately last and physically separated from the read/evidence stack.
