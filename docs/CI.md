# CI policy

The repository is public. GitHub-hosted CI is therefore used as a pull-request quality gate where it materially improves confidence, especially for real Chrome/CDP integration tests.

## Default triggers

- `pull_request` for relevant code, schema, test, workflow and knowledge changes.
- `workflow_dispatch` for explicit re-runs and diagnostics.
- No routine `push` workflow.
- No scheduled workflow unless a future monitoring use case justifies it.

## Quality gate

The collector quality gate performs:

1. Python compilation, unit/contract tests and repository validation.
2. A real Chrome for Testing end-to-end CDP run against a local fixture server.
3. A non-gating A/B/C storage benchmark whose results are published in the workflow summary.

The E2E fixture deliberately uses only loopback services and synthetic secrets. It does not connect to BizMania, export browser state, or perform game writes.

## Reproducibility

GitHub Actions are SHA-pinned. Chrome for Testing is downloaded from the official Google Chrome for Testing manifest for the current Stable channel, and the exact runtime CDP protocol is still discovered and fingerprinted by the collector itself.
