# Change Detector + Promotion Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, replayable offline pipeline that compiles curated BizMan evidence into a versioned Runtime Contract, validates and hashes immutable collector sessions, computes semantic diffs, applies versioned rules, and atomically checkpoints value-free Promotion Bundles through a local SQLite outbox.

**Architecture:** `BaselineCompiler -> RuntimeContract`, `EvidenceReader -> ObservationExtractor`, `SemanticDiff -> versioned RuleEngine`, then one explicit `BEGIN IMMEDIATE` transaction stores checkpoints/findings/pending outbox payloads. A separate materializer writes deterministic bundle files after commit. `analysis_profile_sha256` scopes replay across baseline, normalization/extraction, redaction and rule-version changes.

**Tech Stack:** Python 3.14 preferred (3.11 compatibility retained), stdlib `sqlite3`, `jsonschema[format]==4.26.0`, existing BizMan canonical SHA-256 helpers, JSON Schema Draft 2020-12.

**Spec:** `docs/superpowers/specs/2026-09-07-change-detector-promotion-design.md`

## Global constraints

- Git contains code/schemas/docs/curated knowledge only. `BizManData`, SQLite state, outbox materialization and bundles are operational local data.
- Detector reads only curated repository files plus sanitized collector manifest/JSONL/CAS evidence.
- No LLM before a schema-valid Promotion Bundle exists.
- Missing body evidence is `None/INDETERMINATE`, never an empty signature.
- Baseline/path ambiguity and corrupted evidence fail closed.
- Percent-encoded path text and trailing slashes are preserved exactly in v1.
- Field numeric semantics use ASCII `[0-9]`, not Unicode `isdecimal()`.
- State uses SQLite >=3.37, STRICT, WAL, `trusted_schema=OFF`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`, `application_id=1112359985`, `user_version=1`.
- Write transactions use explicit SQL `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`; no implicit DEFERRED write transaction is permitted.
- Promotion publication uses SQLite transactional outbox; no file/DB dual write in the main transaction.
- Existing collector/foundation behavior must not regress.

## Target file map

```text
tools/bizman_detector/
  __init__.py          semantic version constants
  model.py             immutable contract/observation/diff/finding types
  normalization.py     keys, methods, paths, matcher
  baseline.py          curated corpus -> RuntimeContract
  evidence.py          manifest/JSONL/CAS integrity
  extract.py           event stream -> Observation IR
  diff.py              semantic comparison only
  rules/
    __init__.py        stable rule registry
    http.py
    forms.py
    operations.py
    relations.py
  state.py             SQLite + transactional outbox
  promotion.py         bundle build/schema/materialization
  runner.py            coordinator

tools/detect_changes.py
schemas/promotion-bundle.schema.json

tests/test_detector_model_normalization.py
tests/test_detector_baseline.py
tests/test_detector_evidence.py
tests/test_detector_extract.py
tests/test_detector_diff.py
tests/test_detector_rules.py
tests/test_detector_state.py
tests/test_promotion_bundle.py
tests/test_detector_runner.py
tests/test_detector_integration.py

tools/benchmarks/detector_stream.py
```

---

## Task 1: Semantic model, normalization and deterministic path matching

**Files:**
- Create: `tools/bizman_detector/__init__.py`
- Create: `tools/bizman_detector/model.py`
- Create: `tools/bizman_detector/normalization.py`
- Create: `tests/test_detector_model_normalization.py`
- Modify: `tests/test_detector_baseline.py` to remove assumptions tied to the superseded `CuratedBaseline` API until Task 2 provides `BaselineCompiler`.

**Produces:**

```python
CONTRACT_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = 1
EXTRACTION_VERSION = 1
PROMOTION_SCHEMA_VERSION = 1

class MatchState(Enum):
    KNOWN = "known"
    NOVEL = "novel"
    INDETERMINATE = "indeterminate"
    CONFLICT = "conflict"

@dataclass(frozen=True, slots=True)
class EndpointFamily: ...
@dataclass(frozen=True, slots=True)
class FormSignature: ...
@dataclass(frozen=True, slots=True)
class OperationSignature: ...
@dataclass(frozen=True, slots=True)
class ActionRequestFamily: ...
@dataclass(frozen=True, slots=True)
class RuntimeContract: ...

normalize_method(value: object) -> str
normalize_field_name(value: object, policy: RedactionPolicy) -> str | None
normalize_key_set(values: Iterable[object], policy: RedactionPolicy) -> tuple[str, ...]
normalize_origin_relative_path(value: object) -> str
PathMatcher(patterns: Iterable[str])
PathMatcher.match(path: str) -> PathMatch
```

- [ ] **1.1 RED tests: ASCII key semantics**

Assert:

```python
self.assertEqual(normalize_field_name("product[17]", policy), "product[n]")
self.assertEqual(normalize_field_name("selected[397][2]", policy), "selected[n][n]")
self.assertEqual(normalize_field_name("12345", policy), "{numeric-key}")
self.assertNotEqual(normalize_field_name("١٢٣", policy), "{numeric-key}")
self.assertIsNone(normalize_field_name("clientSecret", policy))
```

- [ ] **1.2 RED tests: paths/methods**

Cover exact trailing slash, `%2F` preservation, invalid absolute URL, invalid fragment/query in path API, method uppercase and invalid whitespace/non-token methods.

- [ ] **1.3 RED tests: specificity and ambiguity**

Fixtures:

```text
/a/b             exact outranks /a/{id}
/a/{id}/c        outranks /{x}/{y}/c
/a/{id} and /{x}/b tie for /a/b -> AmbiguousPathError
```

- [ ] **1.4 Implement minimal immutable model and normalization**

Use ASCII regexes:

```python
_ASCII_INTEGER = re.compile(r"^[0-9]+$")
_INDEX = re.compile(r"\[[0-9]+\]")
_HTTP_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
```

Do not percent-decode paths.

- [ ] **1.5 Implement `PathMatcher`**

Build:

```text
_exact: dict[path, pattern]
_templates_by_segments: dict[int, tuple[CompiledPattern,...]]
```

Templates match one non-empty segment per placeholder. Resolve highest specificity; tied distinct winners raise `AmbiguousPathError`.

- [ ] **1.6 Verify focused tests GREEN**

Run locally when possible; otherwise use one draft-PR validation checkpoint after Tasks 1-2 RED/GREEN batch.

- [ ] **1.7 Commit**

`feat: add detector semantic normalization`

---

## Task 2: Compile curated corpus into Runtime Contract IR

**Files:**
- Create: `tools/bizman_detector/baseline.py`
- Rewrite: `tests/test_detector_baseline.py`

**Consumes:** Task 1 model/normalization.

**Produces:**

```python
@dataclass(frozen=True, slots=True)
class BaselineCompilation:
    contract: RuntimeContract
    baseline_sha256: str
    redaction_policy_sha256: str

class BaselineCompiler:
    @classmethod
    def compile(cls, repo_root: Path, redaction: RedactionPolicy) -> BaselineCompilation
```

- [ ] **2.1 RED: partition loading fails closed**

Test missing declared part, duplicate part path, wrong part record count, malformed record and path escaping outside dataset directory.

- [ ] **2.2 RED: endpoint census alone must not define status/method relationships**

Fixture endpoint aggregate:

```text
path=/x
methods GET,POST
statuses 200,302
```

with application events only `GET->200`, `POST->302`. Assert compiled contract preserves `POST` statuses as `{302}`, not `{200,302}`.

- [ ] **2.3 RED: application-event consistency**

Every curated application event must map to exactly one highest-specificity endpoint pattern. Unmatched or ambiguous event raises `BaselineConsistencyError` including dataset/part/line and path.

- [ ] **2.4 RED: form family/signature semantics**

Sensitive values/fields never enter IR. Runtime-safe subset semantics are represented by multiple `FormSignature`s under `(method, canonical action path)`.

- [ ] **2.5 RED: operations preserve multiple signatures**

Assert at least two distinct compiled signatures exist for `POST /units/vendor/select/`, and indexed keys normalize to `[n]`.

- [ ] **2.6 RED: semantic hash/versioning**

Reordering/repartitioning equivalent source records yields identical `baseline_sha256`. Changing `NORMALIZATION_VERSION` fixture envelope or semantic field yields a different hash.

- [ ] **2.7 RED: redaction semantic fingerprint**

Equivalent policy ordering hashes identically; changing regex text/flags, MIME set or limits changes `redaction_policy_sha256`.

- [ ] **2.8 Implement manifest-driven loaders**

Read only declared partitions. Extract structural fields immediately; never retain examples/values/capture payloads in contract objects.

- [ ] **2.9 Compile endpoint families from application events**

For each concrete event:

```text
canonical_pattern = matcher.match(event.path)
family = (canonical_pattern, normalize_method(event.method))
family.query_keys |= normalized safe keys(event.query.keys())
family.statuses += validated event.status
```

- [ ] **2.10 Compile forms/operations/actions and canonical contract hash**

Use `canonical_sha256()` over an envelope containing contract/normalization versions and sorted semantic records only.

- [ ] **2.11 Full repository baseline fixture GREEN**

Assert current repo compiles, dynamic message path matches correctly, sensitive profile form fields are absent, and `/units/vendor/select/` remains multi-signature.

- [ ] **2.12 Commit**

`feat: compile curated runtime contract`

---

## Task 3: Analysis profile identity

**Files:**
- Modify: `tools/bizman_detector/model.py`
- Modify: `tools/bizman_detector/baseline.py`
- Create: `tools/bizman_detector/rules/__init__.py` with metadata-only registry placeholders (no rule behavior yet)
- Create: `tests/test_detector_analysis_profile.py`

**Produces:**

```python
@dataclass(frozen=True, slots=True)
class RuleDescriptor:
    rule_id: str
    version: int
    kind: str

@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    baseline_sha256: str
    redaction_policy_sha256: str
    normalization_version: int
    extraction_version: int
    rules: tuple[RuleDescriptor, ...]
    sha256: str
```

- [ ] **3.1 RED: profile changes on every interpretation dimension**

Separate assertions for baseline, redaction, normalization, extraction and rule-version changes.

- [ ] **3.2 Implement canonical profile**

Sort rule descriptors by `rule_id`; reject duplicate IDs and nonpositive versions.

- [ ] **3.3 Commit**

`feat: version detector analysis profiles`

---

## Task 4: Streaming EvidenceReader with cryptographic integrity

**Files:**
- Create: `tools/bizman_detector/evidence.py`
- Create: `tests/test_detector_evidence.py`

**Produces:**

```python
@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    session_id: str
    manifest_sha256: str
    evidence_sha256: str
    started_at: str
    ended_at: str
    status: str

class EvidenceReader:
    def inspect(session_id: str) -> EvidenceIdentity
    def iter_events(session_id: str) -> Iterator[Mapping[str, Any]]
    def read_verified_artifact(ref: str) -> bytes
    def iter_finalized(selected: tuple[str,...] = ()) -> Iterator[EvidenceIdentity]
```

- [ ] **4.1 RED: manifest Draft 2020-12 validation**

Use one `Draft202012Validator(..., format_checker=FormatChecker())` initialized once. Reject wrong session directory ID/status/schema.

- [ ] **4.2 RED: safe event paths**

Reject absolute path, `..`, wrong root, symlink escape, non-jsonl and missing file.

- [ ] **4.3 RED: streaming JSONL integrity**

Reject line over `MAX_EVENT_LINE_BYTES`, missing UTF-8, malformed/truncated JSON, non-object, invalid event schema, wrong session ID, non-contiguous sequence and duplicate event IDs.

- [ ] **4.4 RED: actual event-file mutation changes evidence identity**

Same manifest + edited event byte must yield different `evidence_sha256`.

- [ ] **4.5 RED: CAS reference/digest**

Reject malformed/traversal-like refs and actual bytes whose hash differs from the referenced digest. Enforce detector-side max artifact bytes.

- [ ] **4.6 Implement two-pass-safe reader**

`inspect()` hashes/validates stream without retaining raw events. `iter_events()` revalidates event stream for extraction; this favors correctness and bounded memory over a fragile shared mutable iterator.

- [ ] **4.7 Commit**

`feat: verify immutable detector evidence`

---

## Task 5: ObservationExtractor and explicit unknown evidence

**Files:**
- Create: `tools/bizman_detector/extract.py`
- Create: `tests/test_detector_extract.py`

**Produces:**

```python
@dataclass(frozen=True, slots=True)
class HttpObservation:
    ...
    body_keys: tuple[str,...] | None

@dataclass(frozen=True, slots=True)
class FormObservation: ...
@dataclass(frozen=True, slots=True)
class RelationObservation: ...
@dataclass(frozen=True, slots=True)
class ObservationSet: ...

ObservationExtractor(contract, evidence_reader, redaction).extract(identity) -> ObservationSet
```

- [ ] **5.1 RED: no request_body_ref -> `body_keys is None`**

Explicitly assert it never becomes `()`.

- [ ] **5.2 RED: verified form-urlencoded and JSON artifact key extraction**

Parse only already-sanitized CAS; extract names then discard values. JSON accepts object or list-of-objects only, matching collector structural policy.

- [ ] **5.3 RED: malformed/unsupported body evidence becomes hard integrity error when a ref exists**

A persisted body ref claims structurally safe collector output; a referenced artifact that cannot be parsed under its content type is corruption/incompatibility, not UNKNOWN.

- [ ] **5.4 RED: event projection is value-free**

Synthetic query/body/form values must not appear in `repr`, canonical serialization helpers, or any observation field.

- [ ] **5.5 RED: relation sources**

Resolve only same-session referenced `dom.action` and `http.request`; `temporal-only` is retained as non-promotable metadata or omitted consistently. Bound minimal pending source maps and fail closed on cap breach.

- [ ] **5.6 Implement streaming extraction**

Aggregate only compact structural observations. Do not retain headers/raw events/query values.

- [ ] **5.7 Commit**

`feat: extract value-free detector observations`

---

## Task 6: SemanticDiff engine

**Files:**
- Create: `tools/bizman_detector/diff.py`
- Create: `tests/test_detector_diff.py`

**Produces:** data-only `DiffFact`s; no stable rule IDs or persistence.

- [ ] **6.1 RED: endpoint facts**

Known, new path, method added, query key added, status added. Query removal/subset is not considered removal because curated evidence is observational, not a required-parameter schema.

- [ ] **6.2 RED: forms**

Known subset, additive field fact, incompatible signature fact.

- [ ] **6.3 RED: operations**

Exact known signature; unique closest additive query/body delta; ambiguous closest signature -> generic signature novelty; `body_keys=None` -> `INDETERMINATE` and no operation novelty.

- [ ] **6.4 RED: relations/conflicts**

Strong/probable unseen write family fact and action-path/request-path conflict fact. Temporal-only does not become a promotable novelty fact.

- [ ] **6.5 Implement pure diff functions**

No timestamps, SQLite, bundle shape, event values or side effects.

- [ ] **6.6 Commit**

`feat: compute detector semantic diffs`

---

## Task 7: Versioned rule engine and stable findings

**Files:**
- Implement: `tools/bizman_detector/rules/http.py`
- Implement: `tools/bizman_detector/rules/forms.py`
- Implement: `tools/bizman_detector/rules/operations.py`
- Implement: `tools/bizman_detector/rules/relations.py`
- Modify: `tools/bizman_detector/rules/__init__.py`
- Create: `tests/test_detector_rules.py`

**Rule catalog:** exactly the IDs/versions from the spec (`BM-HTTP-001..004`, `BM-FORM-001..002`, `BM-OP-001..003`, `BM-REL-001..002`).

- [ ] **7.1 RED: exact rule-ID set tests**

Each fixture asserts `set(f.rule_id)` equals the exact expected set. Unrelated findings fail the test.

- [ ] **7.2 RED: INDETERMINATE never promotes**

No rule consumes an indeterminate body fact into a Finding.

- [ ] **7.3 RED: change identity**

Same semantic finding across sessions/events has same `change_id`; changing evidence IDs/timestamps does not. Changing rule version/subject/delta does.

- [ ] **7.4 Implement registry and pure rules**

Finding identity canonical input:

```python
{
  "normalization_version": NORMALIZATION_VERSION,
  "extraction_version": EXTRACTION_VERSION,
  "rule_id": descriptor.rule_id,
  "rule_version": descriptor.version,
  "kind": kind,
  "novelty_class": novelty_class,
  "subject": subject,
  "delta": delta,
}
```

- [ ] **7.5 Commit**

`feat: classify detector findings with versioned rules`

---

## Task 8: SQLite STRICT state + transactional outbox

**Files:**
- Create: `tools/bizman_detector/state.py`
- Create: `tests/test_detector_state.py`

**Produces:**

```python
class DetectorState:
    open_rw(path)
    open_read_only_if_exists(path)
    process_session_transaction(...findings...) -> TransactionResult
    pending_outbox() -> tuple[OutboxItem,...]
    mark_materialized(bundle_id, payload_sha256, at) -> None
```

- [ ] **8.1 RED: SQLite version/config**

Require >=3.37. Assert `journal_mode=wal`, `foreign_keys=1`, `trusted_schema=0`, `synchronous=1`, application ID and user version.

- [ ] **8.2 RED: foreign DB/newer version rejected**

Nonzero wrong `application_id` and `user_version > 1` fail closed.

- [ ] **8.3 RED: STRICT schema**

Verify `PRAGMA table_list` marks detector tables strict; invalid datatype inserts fail.

- [ ] **8.4 RED: explicit transaction behavior**

Assert an IMMEDIATE writer blocks/rejects a second writer according to busy timeout and rollback leaves checkpoint/change/outbox unchanged.

- [ ] **8.5 Implement version-compatible explicit transaction connection**

Python >=3.12:

```python
sqlite3.connect(path, timeout=5.0, autocommit=True)
```

Python 3.11 fallback:

```python
sqlite3.connect(path, timeout=5.0, isolation_level=None)
```

Use SQL `BEGIN IMMEDIATE`, `COMMIT`, `ROLLBACK`.

- [ ] **8.6 RED/GREEN: analysis-profile checkpoint semantics**

Same session/profile/evidence is idempotent; same session/profile with changed evidence hash is immutability error; same session under changed profile is processable.

- [ ] **8.7 RED/GREEN: changes audit payload**

Store canonical value-free `identity_json`, occurrence counts and first/last metadata.

- [ ] **8.8 RED/GREEN: outbox is atomic with checkpoint**

Forced exception before COMMIT leaves neither checkpoint nor outbox; successful transaction exposes both together.

- [ ] **8.9 Commit**

`feat: persist detector state with transactional outbox`

---

## Task 9: Promotion schema, deterministic builder and outbox materializer

**Files:**
- Create: `schemas/promotion-bundle.schema.json`
- Create: `tools/bizman_detector/promotion.py`
- Create: `tests/test_promotion_bundle.py`

- [ ] **9.1 RED: Draft 2020-12 schema contract**

Require exact envelope/provenance fields and rule metadata. Use `additionalProperties:false` for stable envelopes.

- [ ] **9.2 RED: deterministic payload and bundle ID**

Identical profile/evidence/first-seen findings yields byte-identical canonical payload and ID.

- [ ] **9.3 RED: no values**

Synthetic secret/query/body/form values must not appear in bundle bytes.

- [ ] **9.4 Implement builder**

Use session `ended_at` as `created_at`; no current wall clock participates in identity/content.

- [ ] **9.5 RED/GREEN: materializer crash cases**

Pending row -> atomic file -> materialized. Existing identical file is accepted/marked. Existing different bytes at same deterministic path is hard integrity error.

Atomic write: same-dir temp, flush, file fsync, replace, parent-directory fsync where supported.

- [ ] **9.6 Commit**

`feat: materialize promotion bundles from outbox`

---

## Task 10: Runner, dry-run and CLI

**Files:**
- Create: `tools/bizman_detector/runner.py`
- Create: `tools/detect_changes.py`
- Create: `tests/test_detector_runner.py`

- [ ] **10.1 RED: runner recovery order**

On normal run, materialize old pending outbox before scanning new sessions so a previous post-commit crash heals immediately.

- [ ] **10.2 RED: session processing order**

Finalized sessions sorted `(started_at, session_id)`; failed sessions skipped/reported; selected session list respected.

- [ ] **10.3 RED: dry-run is truly read-only**

No DB or bundle created. Existing DB may be opened read-only to classify first/repeated findings but receives no changes and pending rows are not materialized.

- [ ] **10.4 Implement coordinator**

Per session:

```text
EvidenceReader.inspect
ObservationExtractor.extract
SemanticDiff
RuleEngine
DetectorState.process_session_transaction
PromotionMaterializer.materialize_pending
```

- [ ] **10.5 Implement CLI**

```text
python tools/detect_changes.py \
  --data-dir ~/BizManData \
  --repo-root . \
  [--session UUID ...] \
  [--dry-run] \
  [--redaction-policy config/redaction-policy.json]
```

Print one machine-readable JSON summary containing profile/baseline/evidence counts, known/novel/indeterminate/conflict counts, first/repeated findings and materialized/pending bundle counts.

- [ ] **10.6 Commit**

`feat: orchestrate deterministic change detection`

---

## Task 11: Synthetic end-to-end integration and privacy audit

**Files:**
- Create: `tests/test_detector_integration.py`
- Modify shared test utilities only if clearly reusable.

- [ ] **11.1 Build a finalized sanitized collector fixture**

Use valid UUIDv7 manifest/event IDs, event schema, JSONL and CAS. Include:

```text
known GET
new query key
known POST body
unknown-body POST
DOM submit + strong relation
synthetic query/body/form secrets that collector-style sanitization removes
```

- [ ] **11.2 End-to-end first run**

Compile real/synthetic contract, process session, verify state and schema-valid materialized bundle.

- [ ] **11.3 End-to-end rerun**

No duplicate bundle/change count inflation beyond intended occurrence semantics for an already checkpointed session.

- [ ] **11.4 Outbox recovery integration**

Simulate committed pending payload without materialized file; runner recovery writes it before new processing.

- [ ] **11.5 Binary/string privacy scan**

Assert synthetic forbidden values are absent from:

```text
promotion bundle bytes
promotion_outbox.payload_json
changes.identity_json
SQLite database bytes (after checkpoint where practical)
```

- [ ] **11.6 Commit**

`test: verify detector end to end`

---

## Task 12: Performance A/B/C benchmark

**Files:**
- Create: `tools/benchmarks/detector_stream.py`
- Optionally modify CI summary step after benchmark is stable.

**Purpose:** validate design choices; never weaken integrity for speed.

- [ ] **12.1 Generate deterministic large synthetic session**

At least 100k small events with realistic request/response/action mix and a small novelty rate.

- [ ] **12.2 Compare A/B/C path matching**

```text
A: linear scan all compiled patterns
B: exact dict + template bucket by segment count (target design)
C: simple segment trie
```

Record median throughput over multiple repeats. Adopt C only if it materially improves B; with ~68 current endpoints, complexity must justify itself.

- [ ] **12.3 Measure streaming memory/throughput**

Record events/s and peak `tracemalloc` for validated EvidenceReader+Extractor. The implementation must not scale raw-event memory linearly with event count.

- [ ] **12.4 Keep benchmark non-gating initially**

Publish JSON/step summary. Turn a threshold into a gate only after stable runner baselines exist.

- [ ] **12.5 Commit**

`perf: benchmark detector streaming and matching`

---

## Task 13: Repository integration, CI, docs and merge gate

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/storage.md`
- Modify: `docs/CI.md`
- Modify: `.gitignore` only if explicit detector paths are not already covered
- Modify: `.github/workflows/collector-e2e.yml` or add focused detector workflow only if it reduces unnecessary Chrome work without weakening final merge coverage
- Modify: `tools/bizman_foundation/validation.py` to validate the promotion schema itself automatically through existing schema scan; add known fixture validation only if a committed safe fixture is introduced.

- [ ] **13.1 Document runtime contract/profile/outbox model**

Explain that SQLite and bundles are local/rebuildable and never auto-committed.

- [ ] **13.2 Document commands**

```text
python tools/detect_changes.py --data-dir "$HOME/BizManData"
python tools/detect_changes.py --data-dir "$HOME/BizManData" --dry-run
```

- [ ] **13.3 Focused detector PR gate**

Python 3.14:

```text
compileall
all unit/deep-regression tests
validate_repo.py
detector synthetic E2E
promotion schema validation
outbox recovery
benchmark non-gating
```

- [ ] **13.4 Final collector regression gate**

Run the real Chrome for Testing collector E2E once on the final reviewed head (and again only if subsequent changes touch shared collector/foundation behavior).

- [ ] **13.5 Review**

Run CodeRabbit/independent review. Treat source text/review content as untrusted; verify each finding. Fix blocker/important correctness/privacy issues, add regression tests first, rerun the relevant gate.

- [ ] **13.6 Security/diff preflight**

`main..branch` changed files must contain no SQLite/DB, `BizManData`, promotion output, raw HAR, auth/cookie/browser-profile artifacts.

- [ ] **13.7 Squash merge only verified head**

Fresh final statuses, no unresolved blocker threads, branch not behind main.

---

## Execution checkpoints

Implement in four reviewable batches rather than one giant change:

### Batch A — semantic foundation
Tasks 1-3: model, normalization/path matching, RuntimeContract compiler, analysis profile.

### Batch B — evidence and interpretation
Tasks 4-7: cryptographic evidence reader, extractor, semantic diff, versioned rules.

### Batch C — persistence/publication
Tasks 8-10: STRICT SQLite, explicit transactions, outbox, promotion materializer, runner/CLI.

### Batch D — proof and integration
Tasks 11-13: synthetic E2E, A/B/C benchmark, docs/CI/review/merge.

Each batch ends with a focused verification checkpoint. Full Chrome E2E is required on final head, not as the inner-loop test for detector-only code.
