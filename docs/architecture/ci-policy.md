# CI policy

The repository is public and uses GitHub-hosted CI as a focused pull-request quality gate where integration confidence justifies it.

- No routine workflow on `push`.
- No scheduled workflow without a concrete monitoring/evaluation requirement.
- Relevant pull requests run the quality gate; `workflow_dispatch` remains available for explicit diagnostics/re-runs.
- `permissions: contents: read` is the default workflow posture.
- Validation starts with compilation, unit/contract tests and repository/schema validation.
- Real Chrome for Testing E2E is used for collector/CDP/action-correlation changes and should be path-scoped as the CI is split further.
- Detector performance benchmarks are non-gating by default; the existing storage flush regression benchmark remains gating in the current PR workflow until the CI split is implemented.
- Large raw HAR captures are never reprocessed in CI and remain outside Git.

The target CI split in `docs/ROADMAP.md` is:

```text
validate
  compile/lint/unit/schema/repo validation

detector-integration
  detector/evidence/index relevant changes

collector-chrome-e2e
  collector/CDP/action/correlation relevant changes

benchmarks
  non-gating by default

agent-evals
  non-gating initially
```

Until that split is implemented, `.github/workflows/collector-e2e.yml` remains the current PR-level quality gate. See `docs/CI.md` for the exact current workflow behavior.
