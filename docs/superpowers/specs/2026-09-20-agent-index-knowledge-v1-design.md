# P2-A Knowledge Retrieval Kernel Design

Status: implementation target after P1 merge `ac465aecc27d12f0dacbe5675367eb5cf51daeee`.

## Goal

Create the first useful deterministic Agent Index slice without coupling it to MCP, UI, session replay or embeddings.

The slice indexes only curated records that already expose stable IDs and explicit evidence refs:

- `knowledge/actions/catalog.json`;
- `knowledge/domain/products/index.json` plus its parts;
- `knowledge/domain/entities.json`.

Expected initial corpus: 333 items.

## Invariants

1. Curated files remain source of truth; SQLite is disposable and rebuildable.
2. Existing `bm.*` IDs remain canonical refs.
3. No model creates or rewrites canonical IDs.
4. Projection is allowlisted by dataset; never flatten arbitrary JSON into search text.
5. Search order is deterministic: exact ref, exact alias, exact normalized title, FTS5/BM25.
6. BM25 score is an implementation detail; result order and refs are the contract.
7. Query size, result count and returned evidence refs are bounded.
8. Identical semantic projected inputs produce the same generation fingerprint.
9. `completed_at` is operational metadata and is excluded from generation identity.
10. `bizman.readmodel` must not import collector, Core or CLI.
11. No embeddings, vector database, fuzzy matching, MCP or session JSONL in P2-A.

## Storage

```text
ref
knowledge_item
alias
knowledge_evidence
knowledge_fts
index_meta
```

SQLite uses a dedicated `application_id`, `user_version=1`, STRICT ordinary tables and FTS5. A rebuild is created in a staged database and atomically replaces the target only after identity/integrity validation.

## Search normalization

Text is NFKC-normalized, case-folded and whitespace-normalized. Exact alias/title matching uses normalized columns. FTS input is tokenized by BizMan code and emitted as quoted tokens; callers never provide raw FTS MATCH syntax.

## Evaluation

`tests/fixtures/retrieval_eval_v1.json` is a deterministic task corpus. Each case contains a query, expected canonical ref and expected evidence ref.

Metrics:

- Recall@1;
- Recall@5;
- MRR;
- evidence correctness.

P2-A begins with high-confidence lookup tasks. Harder ambiguous and natural-language queries are added in P2-B before considering fuzzy or embedding retrieval.
