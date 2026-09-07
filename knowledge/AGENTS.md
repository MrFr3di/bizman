# Knowledge-agent instructions

Read `knowledge/catalog.json` first. For partitioned datasets, open the dataset `index.json` manifest and only the required `part-*` files.

Rules:

1. Never claim an endpoint/action is verified merely because it is referenced by HTML or JavaScript.
2. Preserve source/capture-entry provenance when adding or changing knowledge.
3. Do not commit raw HAR, cookies, session state, authorization headers, browser profiles, `.env`, SQLite/Parquet operational data, or other private runtime state.
4. Prefer stable `bm.*` IDs in cross-references instead of filename-only references.
5. `observed`, `documented`, `discovered-reference`, `inferred`, `hypothesis`, `verified`, `contradicted`, and `deprecated` are materially different confidence states.
6. Update machine-readable knowledge before human-facing indexes when both are changed.
7. Run `python3 tools/validate_repo.py` before proposing a PR that changes `knowledge/**`.
8. Do not replace evidence with summaries: summaries may be regenerated; source-linked observations must remain traceable.
