# BizMan knowledge base

Research repository for structured BizMania browser-game observations, protocol evidence, Wiki mechanics and automation-oriented indexes.

The repository intentionally stores **derived, searchable knowledge instead of raw HAR files**. Raw captures and browser/session state remain private and external; committed source manifests retain SHA-256/provenance so derived observations can be traced back to the supplied captures.

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
tests/                        deterministic foundation/collector tests
docs/                         architecture, provenance and research notes
tools/                        validation, collection, CI fixtures and benchmarks
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

## Passive CDP collector and action context

The live-ingestion implementation now includes:

- UUIDv7 runtime/session identifiers and deterministic fingerprints;
- version-aware Chrome/CDP discovery from `/json/version` and `/json/protocol`;
- passive flattened CDP transport with an explicit command allowlist;
- first-party HTTP/WebSocket normalization with capture-time redaction;
- metadata-only DOM observation for `click`, `change` and `submit` through `Runtime.addBinding` plus `Page.addScriptToEvaluateOnNewDocument`;
- strict action privacy: no form/input values, element text, HTML, cookies, Web Storage or clipboard content are collected;
- deterministic, bounded action-to-HTTP correlation emitted as separate immutable `correlation.action_http` events;
- no LLM or probabilistic model in the collector/correlator hot path; heuristic links remain `inferred` and never become `exact`;
- append-only JSONL event storage plus SHA-256 content-addressed artifacts;
- target/child-target auto-attach and explicit session lifecycle states;
- real Chrome for Testing E2E fixtures and A/B/C storage benchmarks.

Run the collector against a dedicated Chrome profile exposing a local DevTools endpoint:

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/collect_live.py --endpoint http://127.0.0.1:9222 --data-dir ~/BizManData
```

Operational session/event files, browser profiles and future SQLite/Parquet stores stay outside Git.

## Security

This repository is public, so committed content must be safe for public disclosure. Never commit raw HAR/CDP captures, cookies, authorization/session data, browser profiles, `.env`, SQLite/DuckDB databases, Parquet files or other private runtime state. See `SECURITY.md`, `.gitignore` and `config/redaction-policy.json`.

## Validation and CI

Run the local quality gate with:

```bash
python3 -m pip install -r tools/requirements.txt
python3 -m compileall -q tools tests
python3 -m unittest discover -s tests -v
python3 tools/validate_repo.py
```

Because the repository is public, relevant pull requests also run GitHub-hosted CI: compilation/unit/contract validation, a real Chrome for Testing CDP E2E test against loopback fixtures, and a non-gating A/B/C storage benchmark. The Chrome E2E includes a real DOM submit, sanitized form request-body persistence, immutable action-to-HTTP correlation and synthetic-secret non-leakage checks. There is no routine `push` or scheduled CI. See `docs/CI.md`.

## Next development stage

With passive capture and action-context correlation verified, the next layers are automatic change detection and promotion bundles, SQLite current-state projections, Parquet/DuckDB history, reproducible experiments, recommendations and only then guarded write automation.
