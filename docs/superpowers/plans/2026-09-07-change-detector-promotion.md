# Change Detector + Promotion Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic offline detector that compares sanitized collector sessions with curated BizMan knowledge, deduplicates structural novelty in local SQLite, and writes value-free Promotion Bundles for review.

**Architecture:** Separate input validation, normalization/baseline loading, semantic detection, rebuildable state, and bundle persistence into focused modules under `tools/bizman_detector/`. The detector reads existing immutable JSONL/CAS evidence but never changes collector events or curated knowledge. Baseline/change/bundle identities are canonical SHA-256 hashes so processing is reproducible and idempotent.

**Tech Stack:** Python 3.14 preferred (3.11+ compatible), stdlib `sqlite3`, existing `jsonschema==4.26.0`, SHA-256 helpers from `tools.bizman_foundation`, JSON Schema Draft 2020-12.

**Spec:** `docs/superpowers/specs/2026-09-07-change-detector-promotion-design.md`

## Global Constraints

- Git contains code, schemas, docs, and curated knowledge only; detector DB/bundles remain under external `BizManData`.
- Detector input is sanitized collector output only; never read browser cookies/storage/profile data.
- No LLM in reader/extractor/detector/state/bundle path.
- SQLite state requires SQLite >= 3.37, uses `STRICT`, WAL, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`.
- Promotion Bundles contain structural names only, never query/body/form values or arbitrary CAS bytes.
- Unknown/malformed baseline or session input fails closed.
- Existing collector quality gate and real-Chrome E2E must remain green.

---

### Task 1: Semantic normalization and curated baseline

**Files:**
- Create: `tools/bizman_detector/__init__.py`
- Create: `tools/bizman_detector/normalization.py`
- Create: `tools/bizman_detector/baseline.py`
- Test: `tests/test_detector_baseline.py`

**Interfaces:**
- Produces: `normalize_field_name(name, redaction) -> str | None`
- Produces: `normalize_key_set(names, redaction) -> tuple[str, ...]`
- Produces: `EndpointBaseline`, `FormBaseline`, `OperationBaseline`, `ActionBaseline`, `CuratedBaseline`
- Produces: `CuratedBaseline.load(repo_root: Path, redaction: RedactionPolicy) -> CuratedBaseline`
- Produces: `CuratedBaseline.match_endpoint(method: str, path: str) -> EndpointMatch`
- Produces: `CuratedBaseline.sha256: str`

- [ ] **Step 1: Write failing normalization/baseline tests**

Cover exact/trailing-slash matching, `{placeholder}` single-segment matching, digit-only query keys, `[123] -> [n]`, sensitive form-field removal, `/units/vendor/select/` multiple operation signatures, and semantic baseline hash stability across input ordering.

Example assertions:

```python
self.assertEqual(normalize_field_name("product[17]", policy), "product[n]")
self.assertEqual(normalize_field_name("12345", policy), "{numeric-key}")
self.assertIsNone(normalize_field_name("clientSecret", policy))
self.assertTrue(baseline.match_endpoint("GET", "/user/check/message/r/313965").known_method)
self.assertFalse(baseline.match_endpoint("GET", "/user/check/message/r/313965/").matched)
```

- [ ] **Step 2: Run PR unit gate and confirm RED**

Run through the draft PR quality gate. Expected: import failures for `tools.bizman_detector` only; existing collector tests remain green.

- [ ] **Step 3: Implement normalization**

Use segment-aware template compilation:

```python
_PLACEHOLDER = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*\}$")
_INDEX = re.compile(r"\[\d+\]")


def normalize_field_name(name: object, redaction: RedactionPolicy) -> str | None:
    if not isinstance(name, str):
        return None
    value = name.strip()
    if not value or redaction.should_drop_field(value):
        return None
    if value.isdecimal():
        return "{numeric-key}"
    return _INDEX.sub("[n]", value)
```

Compile endpoint templates one segment at a time; placeholders map to `[^/]+` and every literal segment uses `re.escape`.

- [ ] **Step 4: Implement curated baseline loader**

Load partition files only from filenames declared in their index manifests. Form records contribute method, pathname, and redacted normalized field-name sets; never retain `value`. Operations use POST plus normalized query/body keys. Actions retain method/path and normalized safe form/query key classes.

Build `sha256` with `canonical_sha256()` over sorted normalized records, not source file bytes.

- [ ] **Step 5: Run tests and commit GREEN**

Expected: baseline tests and all existing tests pass.

---

### Task 2: Safe session/CAS reader and structural body extraction

**Files:**
- Create: `tools/bizman_detector/session_reader.py`
- Test: `tests/test_detector_session_reader.py`

**Interfaces:**
- Produces: `SessionEvidence(session_id, manifest, manifest_sha256, events, ended_at, status)`
- Produces: `SessionReader(data_dir: Path)`
- Produces: `SessionReader.read(session_id: str) -> SessionEvidence`
- Produces: `SessionReader.iter_finalized(selected: tuple[str, ...] = ()) -> Iterator[SessionEvidence]`
- Produces: `SessionReader.body_keys(event: Mapping[str, Any], redaction: RedactionPolicy) -> tuple[str, ...]`

- [ ] **Step 1: Write failing reader tests**

Create temporary `sessions/<uuid>/manifest.json`, JSONL, and CAS fixtures. Verify:

```python
self.assertEqual([e["sequence"] for e in evidence.events], [0, 1, 2])
self.assertEqual(reader.body_keys(form_request, policy), ("product[n]", "purchase[n]"))
```

Also test missing event files, non-contiguous sequence, duplicate event IDs, mismatched session IDs, failed-session skip, malformed `sha256:` artifact ref, and attempted traversal-like refs.

- [ ] **Step 2: Verify RED**

Expected: `ModuleNotFoundError: tools.bizman_detector.session_reader`.

- [ ] **Step 3: Implement strict session reading**

Resolve event paths from manifest relative to `data_dir` only after `Path.resolve()` containment checks. Resolve CAS exclusively as:

```text
artifacts/sha256/<digest[:2]>/<digest>
```

and reject anything not matching `^sha256:[0-9a-f]{64}$`.

Compute manifest hash with canonical JSON. Require contiguous sequences and unique IDs before returning evidence.

- [ ] **Step 4: Implement body-key extraction**

Read only an event's sanitized `request_body_ref`. Use persisted `Content-Type` to choose JSON object/list-of-objects or form-urlencoded parsing. Extract top-level names, normalize/redact them, discard all values, then release parsed payload.

- [ ] **Step 5: Run tests and commit GREEN**

---

### Task 3: Deterministic change detection

**Files:**
- Create: `tools/bizman_detector/detector.py`
- Test: `tests/test_change_detector.py`

**Interfaces:**
- Produces: immutable `DetectedChange` dataclass with `kind`, `novelty_class`, `confidence`, `subject`, `delta`, `evidence_event_ids`, `fingerprint`, `change_id`
- Produces: `ChangeDetector(baseline, redaction, body_key_resolver)`
- Produces: `ChangeDetector.detect(session: SessionEvidence) -> tuple[DetectedChange, ...]`

- [ ] **Step 1: Write RED tests for every v1 novelty type**

Fixtures must prove known evidence produces zero changes and separately cover:

```text
endpoint.new
endpoint.method_added
endpoint.query_key_added
endpoint.status_added
form.new
form.field_added
operation.new_signature
operation.query_key_added
operation.body_key_added
action_http.new_relation
conflict action/http path
```

Also assert `temporal-only` correlations never create an action relation.

- [ ] **Step 2: Implement endpoint detector**

Index requests by `event_id`/`request_id`; associate responses to requests by request ID. Compare normalized query key classes and response status against the matched endpoint baseline.

- [ ] **Step 3: Implement form/operation detector**

Form matching uses `(method, action pathname)` and safe field-set subset semantics. POST operation matching compares normalized query/body key sets against every same-path operation and selects additive deltas only when there is a unique closest baseline signature; otherwise emit one `operation.new_signature`.

- [ ] **Step 4: Implement action relation detector**

Resolve `action_event_id` and `network_event_id` only from events in the same session. Accept `strong`/`probable`; ignore `temporal-only`. Emit `conflict` when known safe form action path disagrees with linked request path.

- [ ] **Step 5: Stable identity and dedupe inside one session**

Construct identity from `{kind, novelty_class, subject, delta}` only and compute:

```python
fingerprint = canonical_sha256(identity)
change_id = f"chg.{fingerprint}"
```

Merge evidence IDs for identical fingerprints and return changes sorted by `change_id`.

- [ ] **Step 6: Run tests and commit GREEN**

---

### Task 4: Rebuildable SQLite detector state

**Files:**
- Create: `tools/bizman_detector/state.py`
- Test: `tests/test_detector_state.py`

**Interfaces:**
- Produces: `DetectorState(path: Path)` context manager
- Produces: `check_processed(session_id, baseline_sha256, manifest_sha256) -> ProcessedState`
- Produces: `new_change_fingerprints(baseline_sha256, fingerprints) -> frozenset[str]`
- Produces: `commit_session(...) -> None`

- [ ] **Step 1: Write RED state tests**

Verify SQLite version floor, `PRAGMA journal_mode` returns `wal`, tables are STRICT, same session/baseline idempotence, changed-manifest error, baseline replay, occurrence counts, `BEGIN IMMEDIATE` rollback, and schema-version rejection.

- [ ] **Step 2: Implement connection initialization**

Use `sqlite3.connect(path, timeout=5.0)` and explicitly apply the pragmas from the spec. Parse `sqlite3.sqlite_version_info`; reject `< (3, 37, 0)`.

- [ ] **Step 3: Create STRICT schema v1 and state API**

Use parameterized SQL only. `commit_session` starts `BEGIN IMMEDIATE`, re-checks checkpoint under the write lock, increments/upserts every detected change, inserts the processed checkpoint, then commits; any exception rolls back.

- [ ] **Step 4: Run tests and commit GREEN**

---

### Task 5: Promotion Bundle schema and atomic writer

**Files:**
- Create: `schemas/promotion-bundle.schema.json`
- Create: `tools/bizman_detector/promotion.py`
- Test: `tests/test_promotion_bundle.py`

**Interfaces:**
- Produces: `PromotionBundleWriter(data_dir: Path, schema_path: Path)`
- Produces: `build_bundle(...) -> dict[str, Any]`
- Produces: `write_bundle(bundle) -> Path`

- [ ] **Step 1: Write RED bundle tests**

Assert deterministic bytes/ID for identical input, sorted changes/evidence IDs, Draft 2020-12 validity, no values from query/body/form fixtures, and safe deterministic overwrite of an existing identical bundle path.

- [ ] **Step 2: Define Draft 2020-12 schema**

Require exact bundle/version/hash/session fields, `changes` non-empty, stable `chg.<sha256>` IDs, known novelty classes, confidence enum, object `subject`/`delta`, and unique event IDs. Use `additionalProperties: false` for bundle/change envelopes while permitting typed structural subject/delta objects.

- [ ] **Step 3: Implement deterministic builder**

Set `created_at` from finalized session `ended_at`. Hash a canonical payload containing baseline/session/manifest/change content, then set `bundle_id = "promotion." + hash`.

- [ ] **Step 4: Implement atomic persistence**

Write UTF-8 canonical JSON to a same-directory temporary file, flush, `os.fsync()`, `os.replace()`, then fsync the parent directory where supported. Path:

```text
promotions/<baseline[:12]>/<session_id>/<bundle_id>.json
```

- [ ] **Step 5: Run tests and commit GREEN**

---

### Task 6: Coordinator and CLI

**Files:**
- Create: `tools/bizman_detector/runner.py`
- Create: `tools/detect_changes.py`
- Test: `tests/test_detector_runner.py`

**Interfaces:**
- Produces: `DetectionRunner(repo_root, data_dir, redaction, dry_run=False)`
- Produces: `DetectionRunner.run(selected_sessions=()) -> DetectionSummary`

- [ ] **Step 1: Write RED orchestration tests**

Use two temporary finalized sessions: first emits one bundle, second repeats the same change and emits no bundle while incrementing state count. Verify `--dry-run` creates neither DB nor bundle; failed sessions count as skipped.

- [ ] **Step 2: Implement runner order and idempotence**

For each session sorted by `(started_at, session_id)`:

1. check existing checkpoint/manifest hash;
2. detect all changes;
3. query first-seen fingerprints;
4. build/write bundle only for first-seen changes;
5. commit all occurrence counts + checkpoint in SQLite.

In dry-run, perform steps 1-3 against no persistent DB and only report detected counts.

- [ ] **Step 3: Implement CLI**

Arguments:

```text
--data-dir PATH          default ~/BizManData
--repo-root PATH         default repository root
--session UUID           repeatable
--dry-run
--redaction-policy PATH  default config/redaction-policy.json
```

Print one JSON summary and return non-zero on baseline/session/state corruption.

- [ ] **Step 4: Run focused and full tests; commit GREEN**

---

### Task 7: PR integration, documentation, and final gate

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/storage.md`
- Modify: `docs/CI.md`
- Modify: `.gitignore` only if needed for explicit detector paths
- Modify: `.github/workflows/collector-e2e.yml` only if a detector-specific integration step is justified
- Test: existing + detector suites

**Interfaces:** none beyond user-facing commands and CI behavior.

- [ ] **Step 1: Add a synthetic detector integration fixture/test**

Generate sanitized session/manifest/CAS under a temporary directory and execute `tools/detect_changes.py`. Validate the written Promotion Bundle against its schema and rerun to prove idempotence.

- [ ] **Step 2: Document local workflow**

Document:

```text
python tools/detect_changes.py --data-dir "$HOME/BizManData"
python tools/detect_changes.py --data-dir "$HOME/BizManData" --dry-run
```

Explain that bundles and SQLite are local evidence/triage artifacts and are never committed automatically.

- [ ] **Step 3: Full verification on one PR head**

Required commands/jobs:

```text
python -m compileall -q tools tests
python -m unittest discover -s tests -v
python tools/validate_repo.py
collector real-Chrome E2E
storage benchmark (non-gating performance signal)
```

Detector integration must be green on Python 3.14. Inspect changed files for operational data before merge.

- [ ] **Step 4: Review and merge**

Resolve blocker/important review findings, rerun the full gate after code changes, then squash merge only the verified head.
