# AGENTS.md

## Mission
Build a reproducible BizMania research, analytics, and automation system from captured browser evidence. Preserve provenance and never turn an inference into an observed fact.

## Read order
1. `docs/INDEX.md`
2. `knowledge/catalog.json`
3. the local `README.md`/`AGENTS.md` nearest to the files being changed
4. relevant records under `knowledge/`

## Evidence rules
Use these statuses consistently:
- `observed`: directly present in HAR/HTML/JS/JSON.
- `documented`: stated by captured BizMania Wiki/help.
- `inferred`: reasoned from evidence but not directly stated.
- `hypothesis`: unverified proposed explanation.
- `verified`: deliberately reproduced by an experiment.
- `contradicted`: evidence conflicts with the statement.
- `deprecated`: historical knowledge no longer expected to apply.

Every new protocol or mechanic claim must link to at least one source capture + entry, Wiki topic, or experiment.

## Data rules
- Raw HAR files are evidence, not repository content.
- Never commit cookies, Authorization headers, browser profiles, storage state, passwords, `.env`, SQLite/Parquet operational data, or raw captures.
- `knowledge/http/application-events.jsonl` is a sanitized first-party application-event index, not a raw capture.
- Do not silently delete old observations. Add revisions or contradictions.
- Prefer stable IDs and normalized route patterns over copying volatile URLs into prose.
- Keep machine-readable knowledge as the source of truth; Markdown explains it.

## Validation and CI
Run `python tools/validate_repo.py` before proposing changes to `knowledge/`, `schemas/`, or generated indexes.
Do not add always-on GitHub Actions without explicit user approval. The repository owner has limited CI quota.
The existing workflow is manual-only by design.

## Scope
Observe and document first. Recommendations and write automation must be separate from collection. Do not make destructive or state-changing requests merely to test an endpoint unless explicitly approved.
