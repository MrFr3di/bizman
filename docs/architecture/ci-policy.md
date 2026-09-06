# CI policy

CI quota is intentionally conserved.

- No scheduled workflow.
- No workflow on push.
- No automatic workflow on pull requests.
- GitHub validation is manual-only through `workflow_dispatch`.
- Normal validation runs locally with `python3 scripts/validate_knowledge.py`.
- The validator uses only the Python standard library and never reprocesses the large HAR captures.
- The manual GitHub job has read-only repository permissions and a 3-minute timeout.

Add automatic CI only when the project has executable production code whose regression risk justifies the private-repository minutes.
