# BizMania evidence knowledge-base design

## Goal

Turn the three supplied HAR captures into a private, provenance-preserving repository that an agent can navigate without committing the raw captures or consuming CI quota on repeated heavy ingestion.

## Decisions

- Raw HAR remains outside Git; manifests record SHA-256, size and capture window.
- Every one of the 17,108 HAR entries is represented in deterministic network inventories.
- Meaningful HTML is normalized into source-linked snapshots.
- Wiki content is separated from observed runtime behavior and marked `documented`.
- POST requests are stored exactly as decoded form observations with response excerpts and `observed` confidence.
- Endpoint references found in HTML/JavaScript but not executed are marked `discovered-reference`.
- Cross-links use stable `bm.*` IDs plus exact `SOURCE_ID#entry-N` provenance.
- CI never re-ingests the HAR corpus and GitHub Actions remain manual-only unless later production code justifies automatic checks.

## Security boundary

The repository remains private, but derived account/game data is still classified private. Credentials, cookies, raw browser profiles and raw HAR payloads are excluded from Git.
