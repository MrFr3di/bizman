# P2-B Curated Corpus Coverage Design

Status: implementation slice of umbrella issue #9 after P2-A.

## Goal

Expand the deterministic read model from the initial 333 stable curated objects to 590 records without weakening provenance, privacy or retrieval quality.

Added corpora:

- 68 endpoint census records;
- 15 operation signatures;
- 87 captured HTML form signatures;
- 87 Wiki topics.

## Canonical identity

Records that already have stable BizMan refs keep them. New corpus objects use versioned deterministic refs:

```text
bm.endpoint.v1.<semantic-sha-prefix>
bm.operation.v1.<semantic-sha-prefix>
bm.form.v1.<existing-form-id>
bm.wiki.v1.<topic-sha-prefix>
```

Endpoint identity is the endpoint path pattern. Operation identity is the combination of path, sorted query-key names and sorted body-key names because multiple distinct operations may share one path. Form identity uses the already curated deterministic `form_id`. Wiki identity is the topic title.

Legacy operation IDs such as `op-011` and raw form IDs remain exact aliases.

## Provenance

Capture file names are not emitted as final evidence refs. Projectors load `knowledge/sources/captures.json` and translate capture observations to canonical source IDs:

```text
bizmania1.ru.har + entry 296
→ src.har.bizmania.2026-09-06.02#entry-296
```

Unknown capture names fail closed. Session observations use their sanitized `session#seq-N` refs.

## Search privacy discipline

Endpoint, operation and form projectors index structural names only:

- method;
- path;
- query-key names;
- body/form field names;
- field types;
- MIME/resource type labels where useful.

They do not index captured query values, form values, vendor/product/unit IDs from samples, status counts or arbitrary example payload data.

Wiki topics index curated normalized documentation text and related topic names. Empty Wiki bodies are valid: the topic/provenance record is retained and remains searchable by title.

## Manifest integrity

Every partitioned dataset validates:

- declared total count;
- per-part count;
- sequential offsets where the manifest defines offsets;
- unique part paths;
- unique semantic record IDs;
- path containment under the declared dataset root.

The projector does not rely on `validate_repo.py` having run beforehand.

## Versioning

SQLite shape remains schema version 1. Projection semantics change, so `PROJECTION_VERSION` advances from 1 to 2. Existing generated P2-A databases are disposable derived state and must be rebuilt.

## Retrieval gate

The P2-A eval corpus remains mandatory. P2-B adds a second corpus covering endpoint, operation, form and Wiki retrieval. Both corpora must retain:

```text
Recall@1             1.0
Recall@5             1.0
MRR                  1.0
evidence correctness 1.0
```

Expanded corpora are not allowed to silently degrade earlier lookup behavior.
