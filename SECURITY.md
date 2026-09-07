# Security

This repository is public. Network captures, browser state and operational datasets are therefore treated as sensitive local data and must never be committed unless they have been explicitly sanitized for public disclosure.

## Never commit
- `*.har`, `*.har.gz`, `*.har.zst` or raw CDP streams
- cookies, Authorization/session tokens, browser profiles or storage state
- `.env*`, passwords, API tokens or private keys
- raw Chrome/DevTools session exports
- operational `*.sqlite`, `*.db`, `*.duckdb`, `*.parquet`
- local `BizManData/`, raw, artifact, forensic or browser-profile directories

## Source evidence
For supplied HAR files, the repository stores only:
- SHA-256 and capture metadata;
- sanitized first-party application-event indexes/censuses;
- extracted first-party forms/POSTs/routes;
- normalized HTML/Wiki/JS/JSON content needed for research.

Live collection applies `config/redaction-policy.json` before durable normalized runtime events are written. Durable events should contain only required first-party metadata and sanitized bounded bodies/artifact references. WebSocket payloads are not persisted by default.

If a capture or derived artifact contains a credential, remove it before repository promotion and rotate the credential if it was exposed outside the intended local environment or ever committed/published.

## Automation safety
Collection is read-only/passive by default. The collector CDP command allowlist must not include mutating browser/game commands. Write requests belong in a separate guarded executor and must be explicitly classified, logged and validated. Preserve enough evidence to reconstruct why a future write request was generated.

## CI safety
Public CI E2E tests use loopback fixture services and synthetic secrets only. They must not log into BizMania, upload real captures/browser profiles, or perform state-changing game requests.
