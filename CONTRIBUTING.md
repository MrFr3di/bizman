# Contributing

1. Work on a branch and open a pull request; do not use raw HAR files as PR attachments or repository files.
2. Every new protocol claim must include source/capture-entry evidence or be explicitly marked `hypothesis`/`inferred`.
3. Run `python3 tools/validate_repo.py` locally after changing `knowledge/**`, manifests or generated indexes.
4. Avoid CI-heavy workflows. Validation is local by default; GitHub Actions are manual-only because this is a private repository with limited CI quota.
5. Keep generated inventories deterministic: same inputs should produce stable ordering and IDs.
6. For partitioned corpora, update the corresponding `index.json` manifest and preserve declared counts/offsets.
