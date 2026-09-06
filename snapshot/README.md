# Full structured snapshot

The complete derived corpus from the three supplied HAR files is stored as a partitioned Base64 ZIP snapshot because raw HAR files are intentionally excluded from Git.

Snapshot:
- reconstructed file: `bizman-curated-2026-09-06.zip`
- bytes: 212950
- SHA-256: `ea76d8d3147d56046c01e3c705837114bb86eaaf30b0bd2de7f22d156998243c`
- Base64 parts: 16
- encoding: concatenate `parts/*.b64` in lexical order, Base64-decode, verify SHA-256.

The ZIP contains the complete curated tree generated from the captures, including:
- all 555 first-party non-static application event records;
- 17 observed POST bodies;
- endpoint and asset censuses;
- 87 deduplicated HTML form signatures;
- 36 unique first-party JSON responses;
- 732 normalized internal route patterns;
- normalized text for 180 non-Wiki game HTML pages;
- JavaScript SHA index and relevant route/function snippets;
- full normalized text for 87 captured Wiki topics;
- schemas, documentation and validation tooling.

Reconstruct locally:

```bash
python tools/reassemble_snapshot.py
```

This creates `bizman-curated-2026-09-06.zip` in the repository root and verifies SHA-256. The ZIP is a derived sanitized corpus, not a raw HAR.
