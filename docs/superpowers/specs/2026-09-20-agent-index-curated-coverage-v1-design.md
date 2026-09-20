# P2-B Curated Corpus Coverage Design

Base: merged P2-A commit `d0a1689168c312f5d61100a9231688cb99b6d4a5`.

## Goal

Extend the deterministic Agent Index from the first 333 records to the remaining high-value curated read corpora without weakening provenance or copying environment-specific form/request values into search text.

Added corpora:

```text
68 endpoints
15 operation shapes
87 forms
87 Wiki topics
257 added
590 total indexed records
```

## Canonical identity

Existing `bm.*` IDs remain unchanged.

New versioned refs:

- endpoint: `bm.endpoint.v1.<sha256({"path_pattern": ...})>`;
- operation: `bm.operation.v1.<sha256({"method":"POST","path":...,"query_keys":[...],"body_keys":[...]})>`;
- form: `bm.form.v1.<existing form_id>`;
- Wiki: `bm.wiki.v1.<sha256({"path": source.path, "query": canonical_source_query})>`.

The operation source ID such as `op-011` is an alias, not canonical identity, because sequence-style IDs can be renumbered when a source corpus is regenerated.

Endpoint identity intentionally excludes observed methods/counts so additional observations do not create a new endpoint object. Wiki identity uses the source path plus canonical source query rather than content hash. This preserves the topic ref across editorial changes while keeping distinct topics that share `/wikihelp/` separated by `query.topic`.

## Search allowlists

Endpoint:

```text
path_pattern
method names
query-key names
```

Operation:

```text
POST
path
query-key names
body-key names
legacy op ID
```

Form:

```text
method
origin-relative action path
action query-key names
field names
field types
```

Form field values and action query values are forbidden from aliases/body/FTS.

Wiki:

```text
topic
related topic names
curated article text
```

## Provenance

HAR filenames are resolved through `knowledge/sources/captures.json` and stored as existing canonical evidence refs:

```text
src.har.<capture-id>#entry-N
```

Operation observations sourced from a sanitized live session keep:

```text
<session-id>#seq-N
```

Unknown capture filenames, malformed manifests, duplicate part files, count/offset drift, malformed form IDs, absolute form actions and invalid Wiki hashes fail closed.

## Projection version

P2-B changes the semantic projection contract, therefore `PROJECTION_VERSION = 2`.

A P2-A database with projection version 1 is not accepted by P2-B code and must be rebuilt from source corpora.

## Evaluation

P2-B retains the P2-A v1 retrieval corpus and adds `retrieval_eval_v2.json` for endpoint, operation, form and Wiki retrieval.

The intended gate remains:

```text
Recall@1 = 1.0
Recall@5 = 1.0
MRR = 1.0
evidence correctness = 1.0
```

These are high-confidence regression cases, not a claim that arbitrary natural-language retrieval is solved. Harder ambiguous queries are added before any fuzzy/embedding experiment.