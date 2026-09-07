# BizMan knowledge base

Research repository for structured BizMania browser-game observations, protocol evidence, Wiki mechanics and automation-oriented indexes.

The repository intentionally stores **derived, searchable knowledge instead of raw HAR files**. Raw captures remain private and external; committed source manifests retain exact SHA-256/provenance so derived observations can be traced back to the supplied captures.

## Current corpus

Derived from 3 supplied HAR captures:

- 17,108 total network entries
- 16,202 first-party BizMania entries
- 555 significant first-party application events
- 17 observed POST requests
- 68 network endpoint signatures
- 732 internal HTML/JavaScript route patterns
- 87 unique HTML form signatures
- 62 observed form parameters
- 36 unique JSON responses
- 180 normalized non-Wiki game HTML pages
- 914 static-resource census entries
- 29 unique JavaScript resources
- 14 protocol-relevant JavaScript snippets
- 87 captured Wiki topics / 89 Wiki navigation topics
- 303 products
- 18 observed domain entities
- 8 normalized state-changing action families

## Start here

1. `knowledge/catalog.json` — machine-readable master catalog and authoritative dataset counts.
2. `docs/INDEX.md` — human navigation.
3. `AGENTS.md` — rules for Codex/other agents.
4. `knowledge/http/operation-index.json` — high-value protocol/action index.
5. `knowledge/actions/catalog.json` — observed write actions.

## Layout

```text
config/                       capture-time policies
knowledge/
  sources/                    capture provenance and hashes
  actions/                    normalized observed write actions
  domain/                     products and observed game entities
  forms/                      cross-action parameter index
  http/
    application-events/       555 significant first-party events
    endpoints/                68 endpoint signatures
    routes/                   732 discovered internal routes
    forms/                    87 HTML form signatures
    json-responses/           36 JSON responses
    assets/                   914 resource-census records
  javascript/                 script hashes and relevant snippets
  pages/                      180 normalized game HTML pages
  wiki/topics/                87 full Wiki topic texts
schemas/                      structured-data contracts
tests/                        deterministic foundation tests
docs/                         architecture, provenance and research notes
tools/                        local ingestion/validation tooling
```

Large corpora are partitioned behind `index.json` manifests. Each manifest records total counts, part names and offsets/counts where applicable, allowing agents to read only the required slice instead of loading the whole corpus.

## Evidence and confidence

Do not promote assumptions into facts. Knowledge distinguishes:

- `observed` — directly present in captured traffic/HTML/JavaScript;
- `documented` — stated by the captured BizMania Wiki;
- `discovered-reference` — referenced by captured HTML/JavaScript but not observed executing;
- `inferred` — derived from observations;
- `hypothesis` — not yet experimentally verified;
- `verified` — reproduced/confirmed;
- `contradicted` / `deprecated` — retained for history when applicable.

Evidence references use stable capture IDs and entry numbers where possible. Canonical source IDs use the `src.*` namespace; superseded source names remain explicit aliases rather than competing canonical identifiers.

## Ingestion foundation v2

The repository now contains the contracts required for repeatable live ingestion before the CDP collector is added:

- UUIDv7 session/runtime identifier helpers;
- deterministic SHA-256 fingerprints over normalized JSON;
- `config/redaction-policy.json` for capture-time secret removal and bounded first-party body capture;
- Draft 2020-12 schemas for source indexes, sessions, runtime events and redaction policy;
- catalog-driven validation with no hardcoded corpus counts;
- canonical source-ID consistency checks with legacy aliases.

Operational session/event files, browser profiles and future SQLite/Parquet stores stay outside Git.

## Security

Never commit raw HAR, cookies, authorization/session data, browser profiles, `.env`, SQLite/DuckDB databases, Parquet files or other private runtime state. See `SECURITY.md`, `.gitignore` and `config/redaction-policy.json`.

## Validation and CI quota

Install the pinned validation dependency and run tests locally:

```bash
python3 -m pip install -r tools/requirements.txt
python3 -m unittest discover -s tests -v
python3 tools/validate_repo.py
```

GitHub Actions remains deliberately **manual-only** (`workflow_dispatch`). There are no automatic `push`, `pull_request` or scheduled validation runs, so routine data commits do not consume private-repository CI minutes.

## Next development stage

The next stage is a version-aware live Chrome/CDP collector using a dedicated browser profile, capture-time redaction and the session/event contracts above. After passive collection: action correlation, automatic change detection/promotion bundles, SQLite current-state projections, Parquet/DuckDB history, reproducible experiments, recommendations and only then guarded write automation.
