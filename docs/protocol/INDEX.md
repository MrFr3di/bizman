# Protocol index

BizMania traffic in the supplied captures is predominantly classic HTML navigation and HTML form submission. The evidence includes normal documents, a small number of XHR requests for city/map metadata, and observed POST actions. No first-party `fetch` or WebSocket transport was observed in the supplied sessions.

- [Observed write actions](observed-actions.md)
- `knowledge/actions/catalog.json` — 11 normalized observed write-action families.
- `knowledge/http/post-observations.jsonl` — all 17 exact observed POST requests.
- `knowledge/http/endpoints/index.json` — 68 executed network endpoint signatures.
- `knowledge/http/forms/index.json` — 87 deduplicated captured HTML form signatures.
- `knowledge/forms/parameters.json` — 74 cross-action form parameters with sample values.
- `knowledge/http/routes/index.json` — 761 normalized routes referenced by captured HTML/JavaScript.
- `knowledge/javascript/script-index.json` — 29 captured JavaScript resources with hashes/provenance.
- `knowledge/javascript/relevant-snippets.jsonl` — 14 protocol-relevant JavaScript snippets.
- `knowledge/http/operation-index.json` — compact operation-oriented lookup for automation research.

Important distinction: `observed` = present in captured traffic; `discovered-reference` = found in HTML/JavaScript but not necessarily executed in these captures. A discovered reference must not be treated as a safe/replayable API until verified experimentally.
