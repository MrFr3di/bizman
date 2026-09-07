# Contributing

1. Work on a branch and open a pull request; do not use raw HAR/CDP captures as PR attachments or repository files.
2. Every new protocol claim must include source/capture-entry evidence or be explicitly marked with the appropriate non-observed confidence state.
3. Install dependencies with `python3 -m pip install -r tools/requirements.txt`.
4. Before opening/updating a PR, run `python3 -m compileall -q tools tests`, `python3 -m unittest discover -s tests -v` and `python3 tools/validate_repo.py`.
5. Relevant pull requests run the public-repository quality gate: deterministic validation, real Chrome for Testing CDP E2E, and a non-gating A/B/C storage benchmark. Avoid routine `push` and scheduled workflows unless a concrete monitoring use case requires them.
6. Keep generated inventories deterministic: same inputs should produce stable ordering and fingerprints; runtime IDs remain unique UUIDv7 values.
7. For partitioned corpora, update the corresponding `index.json` manifest and preserve declared counts/offsets. Source-ID migrations must preserve previous IDs as explicit aliases rather than silently deleting them.
8. Collector changes must preserve passive-command allowlisting, capture-time redaction, first-party filtering and explicit failure on evidence loss.
9. Runtime `BizManData`, browser profiles, cookies/session state, raw captures, SQLite/DuckDB/Parquet and other operational data must remain outside Git.

See `docs/CI.md` for the CI policy and `SECURITY.md` for public-repository data rules.
