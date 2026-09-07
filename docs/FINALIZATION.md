# Initial HAR corpus finalization

Status: structured initial corpus prepared for merge.

## Source scope

- 3 supplied HAR captures
- 17,108 network entries total
- 16,202 first-party BizMania entries

## Structured corpus

- 555 significant first-party application events
- 17 exact observed POST requests
- 68 network endpoint signatures
- 732 internal HTML/JavaScript routes
- 87 HTML form signatures
- 62 cross-action form parameters
- 36 JSON-response observations
- 180 normalized game HTML pages
- 914 static-resource census records
- 29 unique JavaScript resources
- 14 protocol-relevant JavaScript snippets
- 87 full Wiki topics / 89 navigation topics
- 303 products
- 18 observed domain entities
- 8 normalized write-action families

## Verification

Run locally:

```bash
python3 tools/validate_repo.py
```

GitHub Actions validation is intentionally manual-only (`workflow_dispatch`) to conserve private-repository CI quota. Raw HAR/session/auth data is excluded from Git.
