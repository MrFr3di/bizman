# P4-C C0 — Product Identity Evidence Gate

**Status:** PASS — C0 evidence gate closed 2026-10-03; production goods capture and Current State schema v3 may proceed under `UnitEconomicsV1`.  
**Record date:** 2026-09-30 (initial matrix); **decision date:** 2026-10-03.  
**Design:** `docs/superpowers/specs/2026-09-30-p4c-product-identity-design.md` (amendment §19, 2026-10-03)  
**Pre-C0 audit:** `docs/research/p4c-pre-c0-readiness.md` (historical; describes the pre-C0 state)  
**Runbook:** `docs/research/p4c-c0-runbook.md`

## Purpose

This document records the reproducible C0 decision for product identity. The matrix below was completed on 2026-10-03 from locally authorized first-party evidence: the three private HAR captures identified in `docs/research/captures.md` and the local live response bodies `src.c0.20261003.a1`, `.a2`, `.b1` (plus the `src.c0.20261003.s1`/`.f1` comparisons) identified in `.work/capture-manifest.json`. The PASS recorded here authorizes P4-C production capture and Current State schema v3 strictly within the frozen selector and the `UnitEconomicsV1` contract; it grants no authority to any other surface.

Historical `knowledge/pages` demonstrates that product-facing pages exist, but its normalized visible text cannot prove a stable row-bound numeric product identifier.

## Frozen extraction location

**observed** in `src.c0.20261003.a1`, `src.c0.20261003.a2`, `src.c0.20261003.b1` and the HAR corpus; **verified** by the row-bound research inspector and 21 synthetic structural experiments (`.work/research-experiments.json`; synthetic-only, never a claim about server behavior).

- exactly one visible `table#goods`; two header rows made of `td.tblh` cells; data rows are `tr#pr{N}`;
- the first two `td` cells of a data row each contain exactly one `<a>` whose relative href is exactly `/units/shop/?id=<unit-id>&tab=goods&product=<N>`; both hrefs in a row must carry the same unit id and the same numeric product id;
- the remaining five links per row are JavaScript actions (slider/dialog/buy) and are not identity; no `data-*` attribute carries a product id;
- cross-checks only, never sources of truth: the `tr id="pr{N}"` suffix and the hidden `input name="product[N]"` value equal the extracted `N`.

Both agreeing hrefs must produce the same value or the row fails closed. This is the only location allowed to contribute `product_numeric_id`.

## Research tooling

Use the research-only command against a local response-body file:

```text
bizman probe-product-evidence \
  --repo-root <repo> \
  --source-url 'https://bizmania.ru/units/shop/?id=<unit>&tab=goods' \
  --body-file <local-response.html>
```

The command:
- accepts only the exact approved shop/goods route query (`id`, `tab=goods`); a source URL carrying `product=...` (the product-card surface) is rejected by the route policy;
- rejects control/space-normalized URL variants before URL parsing;
- reads at most 4 MiB locally;
- does not copy the body into BizManData or CAS;
- prints only bounded structural candidate metadata;
- now also supports the frozen row-bound goods selector (`table#goods`, first two `td` cells, agreeing `product=N` hrefs) as a **non-authoritative candidate extractor**; the relative `/products/?id=<positive-int64>` form remains a fallback candidate form when no goods table is present;
- ignores labels, hidden form values, DOM id/name and arbitrary data attributes;
- fails closed on duplicate attribute ambiguity, conflicting/repeated row identities or candidate-count overflow rather than silently truncating.

A candidate is **not** an authoritative product ID by itself, and exit code 0 means only that bounded research inspection completed. Authority for the exact frozen location comes from this decision record and design amendment §19, not from the probe.

## Required real-evidence matrix

| Probe | Required observation | Result |
| --- | --- | --- |
| Shop A / goods | >=2 product rows with row-bound stable numeric IDs | **observed** — `src.c0.20261003.a1` / `.a2`: unit 33670, 10 data rows, two agreeing row-bound `product=N` hrefs per row; the HAR corpus also contains a complete table for the same unit (2 rows). |
| Shop A reload/reorder | same IDs survive ordering/reload | **verified (local comparison)** — `src.c0.20261003.a2` is a reload of unit 33670: full-response SHA-256 differs from `.a1`, but row structure and all 10 numeric product ids are identical. Server-side row reorder was not performed and remains UNKNOWN. |
| Shop B / goods | same structural identity contract on another unit | **observed** — `src.c0.20261003.b1`: unit 33676, 10 data rows under the same selector and agreement rule. HAR corpus: 21 `tab=goods` entries, 18 complete goods tables across 12 units (`bizmania.ru.har` 15/13; `bizmania1.ru.har` 6/5; `bizmaniaFAQ.ru.har` 0/0); 61 distinct numeric product ids. |
| Pagination, if present | omission/completeness semantics measured | **explicitly classified; completeness UNKNOWN** — no captured goods URL (`.work` or HAR) uses `p`, `page`, `offset`, `start` or `limit`, and no pagination control was observed. A missing row is never deletion; completeness cannot be asserted. |
| Supply comparison | determine whether it is a distinct semantic surface | **observed — distinct surface** — `src.c0.20261003.s1` (`tab=supply`, unit 33676) reuses the same numeric product ids, but it is a different surface and inherits no trust from the goods decision. |
| Privacy | identity extraction needs no token/cookie/arbitrary hidden value | **observed** — identity comes only from the relative `product=N` hrefs. No cookie, token or hidden value is required; hidden `input name="product[N]"` is a cross-check, never a source or persisted field. |
| Browser/CDP | Chrome product/version + protocol metadata recorded | **observed with limits** — HAR goods pages carry UA `Chrome/150.0.0.0`; live `.work` pages carry `Chrome/154.0.8037.98` (reduced UA `154.0.0.0`, Windows). The exact CDP protocol version was not obtained (`Browser.getVersion`/`Schema.getDomains` unsupported through the raw interface) and remains UNKNOWN. |

## HAR corpus observations

Measured on 2026-10-03 from the private captures identified in `docs/research/captures.md`.

| Capture | `tab=goods` entries | Complete goods tables |
| --- | ---: | ---: |
| `bizmania.ru.har` | 15 | 13 |
| `bizmania1.ru.har` | 6 | 5 |
| `bizmaniaFAQ.ru.har` | 0 | 0 |
| **Total** | **21** | **18** |

- 12 distinct units with a complete goods table: 13443 (10 products), 13507 (6), 13534 (6), 13548 (6), 13576 (6), 17181 (6), 17184 (6), 17245 (6), 26230 (6), 33196 (4), 33670 (2), 33676 (2);
- 61 distinct numeric product ids; exactly five appear on more than one unit: 384, 416, 427, 428, 460;
- HAR goods pages carry `User-Agent ... Chrome/150.0.0.0 ...`.

## PASS conditions

PASS requires all of the following. All are satisfied by the frozen row-bound `product=N` selector as of 2026-10-03:

1. the same exact structural field identifies products on at least two units — **satisfied**: row-bound `product=N` hrefs on units 33670 (`src.c0.20261003.a1`/`.a2`) and 33676 (`src.c0.20261003.b1`), plus 12 complete-table units in the HAR corpus;
2. the ID is attached to the corresponding product row — **satisfied**: the two agreeing hrefs live in the first two `td` cells of that data row;
3. row order and visible label are not identity — **satisfied**: identity is the query parameter value; labels and row positions never enter the artifact; server-side row reorder was not executed and remains UNKNOWN without weakening the field itself;
4. extraction does not require persisting raw HTML or arbitrary secret-bearing fields — **satisfied**: identity is taken only from the relative href; hidden `product[N]`, DOM `id`/`name` and `data-*` attributes are never identity sources;
5. ambiguity/duplicate behavior is understood — **satisfied**: conflicts, repeats, malformed/duplicate attributes and ambiguity fail closed; 21 synthetic experiments cover the negative cases; no conflict was observed in real rows;
6. pagination/completeness is explicitly classified — **satisfied**: no pagination key (`p`, `page`, `offset`, `start`, `limit`) appears in any captured goods URL; completeness is UNKNOWN; omission is never deletion.

## NO-GO conditions

None of the following applies to the frozen selector; the list remains the stop rule for any later surface change:

- only display names/row positions are available;
- candidate IDs cannot be proven to belong to the row;
- IDs change under harmless reorder/reload;
- extraction requires broad hidden/form/script state;
- different units use incompatible identity semantics without a separately versioned contract;
- completeness/removal is inferred rather than observed.

## Current decision

**PASS (2026-10-03).** The frozen row-bound `product=N` selector satisfies all six PASS conditions (same field on at least two units; row-bound; not order/label; no raw-HTML/secret dependency; fail-closed ambiguity handling; pagination/completeness explicitly classified). Production goods capture and Current State schema v3 may proceed under `UnitEconomicsV1` (design amendment §19) and the §9 coverage/UNKNOWN semantics. No NO-GO condition applies. Supply remains a separate surface with no inherited trust; server-side row reorder, completeness/pagination and deletion remain explicitly UNKNOWN as listed below.

## Anomaly e5307 — product-card surface, not an empty goods snapshot

`bizmania.ru.har` entry 5307 (`https://bizmania.ru/units/shop/?id=17245&tab=goods&product=418`) returned a product-card surface, not a goods table:

- title `Аптека #17245 · Товары`;
- `productsNav` present, zero `table#goods` instances;
- a request form with `maxPrice`, `minQuality`, `targetStockQuantity`, `vendor[0]`, `vendorQuantity[0]`, `vendorPrice[0]`.

**Classification:** a separate surface. It must never be read as an empty goods snapshot, and its `product=418` query parameter must not be promoted as a row-bound goods identity. The research probe already rejects it because its query is not exactly `{id, tab}`. The product-card surface is outside the frozen `UnitEconomicsV1` location and is a separate future contract.

## Remaining UNKNOWNs (explicit)

- **server-side row reorder** — not executed; the selector binds identity per row, so it does not depend on row order, but the server's reorder behavior was not observed.
- **completeness/pagination** — no pagination parameter or control was observed in the captured product lists; completeness of any captured list is UNKNOWN and omission is never deletion.
- **deletion/removal** — outside P4-C; requires a later separately proven complete-snapshot/tombstone contract.
- **supply-quality** — only a bar (title `Снабжение`) exists; no numeric field is authorized.
- **exact CDP protocol version** — `Browser.getVersion`/`Schema.getDomains` unsupported through the raw CDP interface.
- **malformed/duplicate input handling in production** — fail-closed behavior is proven by the research inspector and synthetic experiments only; production parser behavior is a schema v3 implementation-test obligation.
