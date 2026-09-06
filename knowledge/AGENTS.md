# Knowledge-agent instructions

Read `knowledge/catalog.json` first.

Rules:

1. Never claim an endpoint/action is verified merely because it is referenced by HTML or JavaScript.
2. Preserve `source_ref` provenance when adding or changing knowledge.
3. Do not commit raw HAR, cookies, session state, authorization headers or browser profiles.
4. Prefer stable `bm.*` IDs in cross-references instead of filename-only references.
5. `observed`, `documented`, `discovered-reference`, `inferred`, `hypothesis`, `verified` are materially different confidence levels.
6. Update machine-readable knowledge before generated/human-facing indexes if both are changed.
7. Run `python3 scripts/validate_knowledge.py` before proposing a PR that changes `knowledge/**`.
