# Security

This private repository still treats network captures as sensitive.

## Never commit
- `*.har`, `*.har.gz`, `*.har.zst`
- cookies, Authorization tokens, browser profiles/storage state
- `.env*`, passwords, API tokens
- raw Chrome/DevTools sessions
- operational `*.sqlite`, `*.db`, `*.parquet`
- local data directories

## Source evidence
For supplied HAR files, the repository stores only:
- SHA-256 and capture metadata;
- sanitized first-party application-event indexes/censuses;
- extracted first-party forms/POSTs/routes;
- normalized HTML/Wiki/JS/JSON content needed for research.

If a future capture contains a credential, sanitize before deriving repository data and rotate the credential if it was exposed outside the intended local environment.

## Automation safety
Collection should be read-only by default. Write requests must be explicitly classified, logged, and separated from collection. Preserve enough evidence to reconstruct why a request was generated.
