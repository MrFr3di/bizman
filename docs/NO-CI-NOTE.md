# CI note

This branch intentionally does not enable automatic validation on push or pull request. The existing validation workflow is `workflow_dispatch` only; routine verification is local via `python3 tools/validate_repo.py`.
