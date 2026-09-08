# CI policy

The repository is public. GitHub-hosted CI is used as a pull-request quality gate where it materially improves confidence, especially for deterministic detector integration and real Chrome/CDP regression tests.

## Default triggers

- `pull_request` for relevant code, schema, test, workflow and knowledge changes.
- `workflow_dispatch` for explicit re-runs and diagnostics.
- No routine `push` workflow.
- No scheduled workflow unless a future monitoring use case justifies it.

## Quality gate

The `collector-quality-gate` workflow currently performs three jobs.

### 1. `validate` — gating

Python 3.14 runs:

1. `python -m compileall -q tools tests`.
2. Full `unittest` discovery, including foundation/collector/correlation and detector deep-regression tests.
3. `python tools/validate_repo.py`, which validates committed structured knowledge and schemas.

The detector portion of this gate covers, among other invariants:

- deterministic RuntimeContract/baseline/profile identity;
- Draft 2020-12 manifest/event/Promotion Bundle contracts;
- traversal and symlink-escape rejection for evidence roots;
- actual JSONL/CAS byte hashing and corruption fail-closed behavior;
- `UNKNOWN != EMPTY` body semantics;
- semantic diff separated from stable/versioned rules;
- SQLite STRICT/WAL compatibility, `BEGIN IMMEDIATE`, checkpoints and change identity;
- transactional outbox rollback/recovery;
- value-free canonical Promotion Bundles and privacy rejection;
- synthetic filesystem → evidence → extraction → diff → rules → SQLite/outbox → bundle E2E;
- rerun/idempotence and downstream synthetic-secret byte scans.

### 2. `collector-e2e` — gating

After `validate`, a real Chrome for Testing end-to-end CDP run executes against a local fixture server. The fixture exercises first-party HTTP/WebSocket capture plus a real DOM form submit and verifies the resulting immutable action-to-HTTP correlation.

The E2E fixture deliberately uses only loopback services and synthetic secrets. Its form contains both a safe field and a synthetic secret field; the verifier requires the DOM action metadata to retain only the safe field name, the persisted URL-encoded request body to retain only the safe value, and every synthetic secret value to be absent from normalized events and all persisted CAS artifacts. It does not connect to BizMania, export browser state, or perform game writes.

The collector is terminated by SIGINT after the bounded E2E observation window, so `cancelled` is an accepted and schema-valid terminal session status for this test. The verifier still requires contiguous event sequencing and complete evidence for the expected fixture traffic before accepting the run.

This job is the final shared collector/foundation regression gate for detector changes. It should be rerun on the final reviewed head and again only when subsequent changes touch shared collector/foundation behavior or the E2E/workflow itself.

### 3. `benchmark` — storage gating, detector performance non-gating

The benchmark job publishes two families of measurements:

- the existing A/B/C collector storage flush benchmark;
- the Change Detector benchmark from `tools/benchmarks/detector_stream.py`.

The detector benchmark uses at least 100,000 deterministic synthetic events by default and compares:

- A — linear scan of all compiled route patterns;
- B — current exact-dict + template bucket-by-segment-count `PathMatcher`;
- C — simple segment trie.

A/B/C matcher outputs must be semantically equivalent before timing is reported. The benchmark records median requests/s, then runs the real validated `EvidenceReader + ObservationExtractor` path and reports source events/s, effective validation events/s and peak `tracemalloc` memory. Integrity checks are never disabled for benchmark speed.

Detector performance is intentionally `continue-on-error`/non-gating initially because shared GitHub runners are noisy. Correctness remains gating through the unit/integration suite; benchmark results are evidence for future optimization, not a reason to weaken validation.

## Local detector commands

Install the committed runtime/test dependencies and run the same deterministic validation surface with:

```bash
python3 -m pip install -r tools/requirements.txt
python3 -m compileall -q tools tests
python3 -m unittest discover -s tests -v
python3 tools/validate_repo.py
```

Run the detector itself with:

```bash
python3 tools/detect_changes.py --data-dir "$HOME/BizManData"
python3 tools/detect_changes.py --data-dir "$HOME/BizManData" --dry-run
```

Detector state and bundles remain local/rebuildable under `BizManData` and are never CI artifacts intended for commit.

## Reproducibility

GitHub Actions are SHA-pinned. Chrome for Testing is downloaded from the official Google Chrome for Testing manifest for the current Stable channel, and the exact runtime CDP protocol is still discovered and fingerprinted by the collector itself. Runtime/Page instrumentation parameters are capability-detected from that discovered protocol rather than assumed from a fixed tip-of-tree schema.

Detector interpretation is separately replay-scoped through `analysis_profile_sha256`, which includes baseline/redaction and versioned normalization/extraction/rule semantics. A performance result therefore does not replace the semantic/profile identity required for reproducible detector outputs.
