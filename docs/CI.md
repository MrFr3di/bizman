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
2. A real Chrome for Testing end-to-end CDP run against a local fixture server. The fixture exercises first-party HTTP/WebSocket capture plus a real DOM form submit and verifies the resulting immutable action-to-HTTP correlation.
3. A non-gating A/B/C storage benchmark whose results are published in the workflow summary.

The E2E fixture deliberately uses only loopback services and synthetic secrets. Its form contains both a safe field and a synthetic secret field; the verifier requires the DOM action metadata to retain only the safe field name, the persisted URL-encoded request body to retain only the safe value, and every synthetic secret value to be absent from normalized events and all persisted CAS artifacts. It does not connect to BizMania, export browser state, or perform game writes.

The collector is terminated by SIGINT after the bounded E2E observation window, so `cancelled` is an accepted and schema-valid terminal session status for this test. The verifier still requires contiguous event sequencing and complete evidence for the expected fixture traffic before accepting the run.

## Reproducibility

GitHub Actions are SHA-pinned. Chrome for Testing is downloaded from the official Google Chrome for Testing manifest for the current Stable channel, and the exact runtime CDP protocol is still discovered and fingerprinted by the collector itself. Runtime/Page instrumentation parameters are capability-detected from that discovered protocol rather than assumed from a fixed tip-of-tree schema.
