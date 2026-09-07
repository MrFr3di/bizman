# Contributing

1. Work on a branch and open a pull request; do not use raw HAR/CDP captures as PR attachments or repository files.
2. Every new protocol claim must include source/capture-entry evidence or be explicitly marked with the appropriate non-observed confidence state.
3. Install validation dependencies with `python3 -m pip install -r tools/requirements.txt`.
4. Run `python3 -m unittest discover -s tests -v` and `python3 tools/validate_repo.py` after changing `knowledge/**`, `schemas/**`, ingestion contracts, manifests or generated indexes.
5. Avoid CI-heavy workflows. Validation is local by default; GitHub Actions are manual-only because this is a private repository with limited CI quota.
6. Keep generated inventories deterministic: same inputs should produce stable ordering and fingerprints; runtime IDs remain unique UUIDv7 values.
7. For partitioned corpora, update the corresponding `index.json` manifest and preserve declared counts/offsets. Source-ID migrations must preserve previous IDs as explicit aliases rather than silently deleting them.
