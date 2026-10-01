# P4-C pre-C0 readiness audit

**Status:** PRE-C0 / RESEARCH ONLY / NO AUTHORITY GRANTED  
**Repository baseline:** `0945bbe90e90d8460a4776d1e79c39abe44e7d38`  
**Purpose:** finish every safe repository-side preparation step before a locally authorized real-response C0 run.

This document is deliberately non-authoritative. It records what can be established from
committed repository material and synthetic tests. It does **not** prove a live row-bound
product identifier and it does not authorize ProductEvidenceV1, production goods capture,
Current State projection v3, or unit-product materialization.

## 1. C0 probe hardening review

The research probe is intentionally narrower than a browser DOM scraper. It accepts only:

- HTTPS;
- exact host `bizmania.ru`;
- exact path `/units/shop/`;
- exactly one ASCII positive signed-64-bit `id`;
- exactly one `tab=goods`;
- no userinfo, explicit port, fragment, percent-encoded path/query component, extra query key,
  or C0/space/DEL character in the supplied URL.

The transient HTML inspector recognizes only exact relative
`/products/?id=<positive-int64>` anchor hrefs. It does not emit labels, input values, DOM
`id`/`name`, arbitrary `data-*` attributes, scripts, styles, templates, SVG, iframe or
other suppressed subtree content.

### Additional fail-closed behavior added in the pre-C0 slice

1. Reject URL input containing ASCII C0 controls, spaces or DEL before `urlsplit` can
   normalize/strip them.
2. Treat duplicate attribute names as ambiguous and suppress that element/subtree rather than
   allowing one parser-selected value to become evidence.
3. Treat more candidates than the configured bound as an error. The probe no longer silently
   truncates a page and presents the prefix as if it were complete.
4. Add regression coverage for malformed hidden nesting, duplicate href/style attributes,
   candidate overflow and oversized local input.
5. Keep CLI failures value-free: unsupported routes and parser failures expose only stable
   failure classes/messages, never the supplied URL, labels or response content.

### Deliberate limitations that remain until real C0

The inspector is a research candidate finder, not an authoritative extractor.

- A product href anywhere in an allowed visible subtree is still only a candidate. C0 must prove
  its structural binding to the correct product row.
- `HTMLParser` is not Chrome's computed DOM/CSS visibility engine. C0 must therefore use real
  browser evidence and freeze an exact structural location rather than assuming the research
  parser models every browser rendering edge case.
- Multiple different product IDs are expected on a goods page and are not themselves
  ambiguity. Ambiguity means the structural location cannot be proven, duplicate/malformed
  attributes disagree, the bounded inspection overflows, or the same apparent row can map to
  conflicting identities.
- Exit code 0 means only that the research inspection completed. It is never a C0 PASS signal.

## 2. Historical product-surface census

The committed `knowledge/pages` corpus contains 180 normalized text records. It preserves
route/query metadata, title, normalized visible text and an HTML SHA-256, but not original DOM
attributes or raw HTML. Therefore it can establish surface existence and useful sampling targets,
but cannot prove product identity.

Verified route census:

| Surface | Records | Distinct units visible from corpus | Authority |
| --- | ---: | ---: | --- |
| `/units/shop/?tab=goods` | 19 | 12 | historical text only |
| `/units/shop/?tab=supply` | 15 | 12 | historical text only |
| `/units/shop/?tab=divisions` | 13 | not used for C0 identity | historical text only |
| `/units/shop/?tab=reports` | 14 | not used for C0 identity | historical text only |
| `/units/shop/?tab=shop` | 8 | not used for C0 identity | historical text only |
| `/units/shop/` with no tab | 4 | not used for C0 identity | historical text only |
| `/units/shop/?tab=service` | 1 | not approved | historical text only |
| `/units/service/?tab=goods` | 4 | 3 | separate semantic surface |
| `/units/service/?tab=service` | 2 | 2 | separate semantic surface |
| `/units/service/?tab=divisions` | 3 | 3 | separate semantic surface |
| `/units/service/?tab=reports` | 3 | 3 | separate semantic surface |

The 12 historical shop/goods unit IDs are:

`13443`, `13507`, `13534`, `13548`, `13576`, `17181`, `17184`,
`17245`, `26230`, `33196`, `33670`, `33676`.

Two units are especially useful for future stability testing:

- shop `13548`: six historical goods observations, including five repeats in the later corpus;
- shop `17245`: three historical goods observations.

These repeated observations do **not** prove numeric identity, but they make good C0 targets for
reload/reorder/stability checks. Units `33670` and `33676` are also useful as two distinct
shop types with small visible product sets in the historical text.

The four service/goods observations include units `13597` (twice), `17224` and `26238`.
Service goods remain outside the approved shop/goods contract. Similar visible tables are not
evidence that service and shop share identity semantics.

### What historical evidence cannot answer

The committed representation cannot establish:

- whether a numeric product ID exists in the original row markup;
- whether an ID-bearing href/attribute is attached to the same row as a visible product;
- whether the same identity survives browser-side reorder or pagination;
- whether hidden/form/script state contains misleading IDs;
- whether omission means absence, pagination or partial rendering;
- whether shop/supply and service/goods share the same semantic identity contract.

Those are C0 questions and must remain local until sanitized conclusions are recorded.

## 3. Curated product catalog audit

The current curated catalog contains exactly 303 product records across nine parts.

Verified identity properties:

- 303 unique `bm.product.*` catalog IDs;
- 303 unique slugs;
- 64 products have one observed numeric ID;
- 239 products have no numeric ID;
- 64 numeric ID values total;
- no duplicate numeric ID maps to more than one catalog product;
- no current product has multiple numeric IDs;
- all current numeric IDs are positive and fit the signed 64-bit grammar used by P4-C.

Display names are not unique. The two current collisions are:

- `Настольный компьютер`: `bm.product.desktop` (unmapped) and
  `bm.product.computer` (numeric ID 125);
- `Смартфон`: `bm.product.mobile` (unmapped) and
  `bm.product.smartphone` (numeric ID 129).

This is direct evidence that display-name resolution cannot be authoritative.

### Candidate catalog semantic fingerprint

Do not bind this into Current State before C0 PASS. The candidate semantic input for v3 is:

```text
resolver_version = numeric-exact-v1
entries = sorted(
    (observed_numeric_id, catalog_id)
    for every curated numeric mapping
)
```

The future fingerprint payload should include only data capable of changing resolution:

- resolver version;
- observed numeric ID;
- curated catalog ID.

It should exclude:

- display name;
- slug unless the resolver actually consumes it;
- categories;
- evidence-note strings;
- part/file path;
- JSON formatting;
- mtime/order in source files.

Construction must fail closed if one numeric ID maps to multiple catalog IDs. An unmapped live
numeric ID remains a valid observed identity with nullable catalog resolution.

## 4. C0 local capture kit

Raw authenticated bodies stay outside the repository. On Windows, use a temporary directory
rather than a path under the checkout:

```powershell
$C0 = Join-Path $env:TEMP "bizman-p4c-c0"
New-Item -ItemType Directory -Force $C0 | Out-Null
```

Collect these cases from an authorized logged-in Chrome session:

1. **A1:** shop A, `tab=goods`, at least two visible product rows.
2. **A2:** the same shop after reload and, if the UI permits it, a harmless row-order change.
3. **B1:** a different shop, `tab=goods`.
4. **P1:** another page/pagination state if goods pagination exists.
5. **S1:** same-unit `tab=supply` only for semantic comparison; it is not accepted by the
   current goods probe and must not be promoted automatically.

For each goods body copied locally from the real Network response, run:

```powershell
uv run bizman probe-product-evidence `
  --repo-root "$PWD" `
  --source-url "https://bizmania.ru/units/shop/?id=<UNIT>&tab=goods" `
  --body-file "$C0\<CASE>.html"
```

Keep the HTML and any browser export only in `$C0`. Do not commit them, paste them into an
issue, or upload them as CI artifacts.

Record separately:

- Chrome product/version;
- CDP protocol/version metadata when the body is obtained through CDP;
- exact unit ID and case label;
- candidate numeric IDs reported by the probe;
- the exact structural relation observed in DevTools between each candidate href and its row;
- whether hidden/inactive markup contains competing product-like IDs;
- whether A1/A2 preserve identity under reload/reorder;
- whether pagination exists and what one page omission means.

A safe worksheet can contain numeric IDs and structural selector descriptions, but no cookies,
tokens, headers, arbitrary hidden/form values, raw labels when they are unnecessary, or raw HTML.

### PASS interpretation

C0 PASS requires a reviewer to be able to state, from the local observations, one exact
row-bound field/location that:

- identifies the corresponding product on at least two units;
- remains stable across harmless reload/order change;
- can be extracted without persisting broad hidden/form/script state;
- has understood duplicate/ambiguity behavior;
- has explicit pagination/completeness semantics.

A successful probe command or a matching catalog ID is not sufficient.

## 5. C1/C2 architecture readiness review

No C1/C2 production code is added before C0. The current v2 architecture nevertheless exposes
the exact seams that v3 will need.

### 5.1 Replay seam

`current/replay.py` already orders finalized sessions by
`(started_at, session_id)` and validates contiguous event sequence. Company/unit latest-positive
projection is folded during that replay.

Future product replay should be a second deterministic reducer over the same verified event
stream. It must not be implemented in `collector`, because the repository import contracts
correctly keep `current` independent from collector implementation details.

### 5.2 Product coverage is not global projection status

Current v2 metadata has global `ready/stale`. P4-C needs per-unit/per-surface
`ready/unknown/stale`.

Do not add `unknown` to the existing global status merely to represent missing product
observations. In v3:

- global status continues to describe whether the projection itself is internally compatible;
- product-surface state describes evidence sufficiency for one unit/surface;
- no goods evidence => product surface `unknown`, not global corruption;
- incompatible evidence for a recognized product surface => that surface `stale`;
- a verified company roster being ready never implies goods coverage is ready.

Whether any product-surface stale state also raises global stale must be frozen as an explicit
v3 rule rather than inferred ad hoc.

### 5.3 Input fingerprint seam

Current v2 `current_input_fingerprint` binds:

- projection identity/version;
- analysis-profile SHA-256;
- replayed immutable session semantics.

That is sufficient for P4-A/B but insufficient once catalog resolution can change output without
changing evidence. Projection v3 therefore needs a required catalog-semantic SHA-256 (or an
equivalent explicit semantic input) in the fingerprint contract.

Do not hash catalog file bytes wholesale. Formatting/path-only changes must not change semantic
identity.

### 5.4 Orphan observation seam

An allowed goods observation may appear before its P4-B unit roster, or the unit may never become
verified in the replay set.

The v3 design must preserve explainability without fabricating an FK-backed association. Before
implementation, freeze one explicit representation for orphan observations. Acceptable designs
must preserve:

- unit ID from request identity;
- observed product numeric ID;
- source session/sequence/time;
- artifact SHA/schema;
- an unresolved/orphan reason.

The authoritative `unit_product` relation itself remains FK-backed and must never join by name.

### 5.5 v2 -> v3 migration seam

The current migration helper recognizes the previous schema through exact table/column checks.
When v3 is introduced, do not generalize that to "whatever `USER_VERSION - 1` happens to be".
Freeze an explicit exact-v2 recognizer, build a sibling v3 database from immutable evidence,
validate it, then atomically replace.

Unknown, foreign or newer schemas remain fail-closed.

## 6. Documentation contract correction

C0 is only the evidence decision.

Before C0 PASS it is valid to freeze:

- route grammar;
- privacy boundary;
- candidate inspection behavior;
- PASS/NO-GO matrix;
- future artifact bounds as design constraints.

Before C0 PASS it is **not** valid to create or treat as production authority:

- a committed ProductEvidenceV1 schema file;
- a production goods response-body capture allowlist;
- a product reducer/materializer;
- projection/schema v3.

Issue #40 and the implementation plan should use this same ordering: research contract first,
ProductEvidenceV1 implementation only after the real C0 gate passes.

## 7. Independent Q1 / issue #8 status

The code-side coverage work is already complete on `main`:

- Python 3.14 full tests run under pinned Coverage.py 7.16.1;
- `coverage.xml` is generated;
- the report is uploaded as `python-coverage`;
- `sonar-project.properties` points Python coverage at `coverage.xml`;
- `docs/CI.md` documents the migration.

The remaining work is repository/Sonar administration, not application code:

- enable a `main` ruleset/branch protection;
- require PRs, resolved conversations and up-to-date branch;
- require `validate`, `compatibility`, `collector-e2e`, `benchmark`;
- block force push and deletion;
- disable Sonar Automatic Analysis;
- configure `SONAR_TOKEN`, `SONAR_ORGANIZATION`, `SONAR_CI_ENABLED=true`;
- verify a fresh PR consumes `coverage.xml`.

The current GitHub connector exposes ruleset/protection reads but no administration write action,
so those external settings remain a manual account/repository action and should stay outside the
P4-C branch.

## Exit condition for this pre-C0 slice

This preparation slice is complete when:

- probe hardening tests are green in CI;
- this audit is reviewed;
- issue #40 no longer implies that production ProductEvidenceV1 schema implementation belongs
  before C0 PASS;
- no production capture/current-state schema semantics have changed;
- the next P4-C action is the local A1/A2/B1/P1 evidence run.
