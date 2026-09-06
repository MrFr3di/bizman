# Data architecture

The repository is a versioned knowledge base, not a storage location for raw browser captures.

1. **Raw evidence** — original HAR files stay outside Git and are referenced by SHA-256 in `knowledge/sources/`.
2. **Inventory** — every network entry is represented by a source-linked inventory record.
3. **Evidence** — write actions, forms, scripts, page snapshots and wiki pages retain exact source-entry references.
4. **Knowledge** — endpoints, products, entities and relations use stable IDs and explicit confidence.
5. **Automation code** — future code must consume the knowledge layer, not silently invent protocol behavior.

This keeps raw sensitive captures out of Git while preserving provenance and enough structured evidence to reproduce analysis from the original files locally.
