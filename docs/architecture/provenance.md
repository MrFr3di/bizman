# Provenance model

A source reference has the form `SOURCE_ID#entry-N` and points to one entry in a specific HAR whose file SHA-256 is recorded under `knowledge/sources/`.

Knowledge records use stable IDs:

- `bm.endpoint.*` — HTTP endpoints
- `bm.form.*` — HTML form signatures
- `bm.observation.*` — captured events/actions
- `bm.snapshot.*` — normalized HTML snapshots
- `bm.wiki.*` — Wiki articles
- `bm.product.*` — product entities

Confidence values:

- `observed` — directly present in captured network traffic or HTML.
- `documented` — stated in the captured BizMania Wiki.
- `discovered-reference` — route referenced by captured HTML/JavaScript but not executed in the supplied captures.
- `inferred` / `hypothesis` / `verified` — reserved for research conclusions and must not be silently promoted.
