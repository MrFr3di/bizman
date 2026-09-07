# BizMania evidence knowledge-base design

## Goal

Turn the three supplied HAR captures into a private, provenance-preserving repository that an agent can navigate without committing the raw captures or consuming CI quota on repeated heavy ingestion.

## Decisions

- Raw HAR remains outside Git; manifests record SHA-256, size and capture window.
- Capture metadata records the complete 17,108-entry source scope; 555 meaningful first-party application events are indexed individually, 914 first-party static paths are represented by an aggregated asset census, and third-party noise remains summarized at capture level.
- Meaningful HTML is normalized into 180 source-linked page records.
- Wiki content is separated from observed runtime behavior and marked `documented`.
- POST requests are stored as decoded form observations with exact source-entry evidence and `observed` confidence.
- Endpoint references found in HTML/JavaScript but not executed are marked `discovered-reference`.
- Cross-links use stable `bm.*` IDs plus exact `SOURCE_ID#entry-N` provenance where available.
- CI never re-ingests the HAR corpus and GitHub Actions remain manual-only unless later production code justifies automatic checks.

## Security boundary

The repository remains private, but derived account/game data is still classified private. Credentials, cookies, raw browser profiles and raw HAR payloads are excluded from Git.
