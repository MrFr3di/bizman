# Contributing

1. Work on a branch and open a pull request; do not use raw HAR files as PR attachments or repository files.
2. Every new protocol claim must include a `source_ref` or be explicitly marked `hypothesis`/`inferred`.
3. Run `python3 scripts/validate_knowledge.py` locally after changing `knowledge/**`.
4. Avoid CI-heavy workflows. Validation is local by default; GitHub Actions are manual-only because this is a private repository with limited CI quota.
5. Keep generated inventories deterministic: same inputs should produce stable ordering and IDs.
