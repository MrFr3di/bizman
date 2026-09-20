# Contributing

1. Work on a branch and open a pull request; do not use raw HAR/CDP captures as PR attachments or repository files.
2. Every new protocol claim must include source/capture-entry evidence or be explicitly marked with the appropriate non-observed confidence state.
3. Use `pyproject.toml` plus the committed `uv.lock` as the dependency authority. Run `uv lock --check` and `uv sync --locked`; do not add parallel pip requirements files.
4. Before opening or updating a PR, run the canonical local gate: `uv run ruff check src`, `uv run lint-imports`, `uv run python -m compileall -q src tools tests`, `uv run python -m unittest discover -s tests -v`, and `uv run python tools/validate_repo.py`.
5. Relevant pull requests run `.github/workflows/collector-e2e.yml`: Python 3.14 full validation, Python 3.11 compatibility, real Chrome for Testing CDP E2E, storage gating and detector benchmark reporting. The same workflow supports `workflow_dispatch`; avoid duplicate manual validation workflows, routine `push` workflows and scheduled workflows unless a concrete use case requires them.
6. Keep generated inventories deterministic: same inputs should produce stable ordering and fingerprints; runtime IDs remain unique UUIDv7 values.
7. For partitioned corpora, update the corresponding `index.json` manifest and preserve declared counts/offsets. Source-ID migrations must preserve previous IDs as explicit aliases rather than silently deleting them.
8. Collector changes must preserve passive-command allowlisting, capture-time redaction, first-party filtering, bounded CDP command waits and explicit failure on evidence loss.
9. Runtime `BizManData`, browser profiles, cookies/session state, raw captures, SQLite/DuckDB/Parquet and other operational data must remain outside Git.
10. Before merge, verify the exact reviewed head: all required jobs green, branch current with `main`, and all substantive review threads resolved.

See `docs/CI.md` for the CI policy and `SECURITY.md` for public-repository data rules.
