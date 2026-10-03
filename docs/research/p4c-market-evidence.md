# S4 Market Analytics Evidence Gate — Retail Market, Retail Prices, Vendors

**Status:** PASS — S4 market evidence gate closed 2026-10-03. This record authorizes read-only structural extraction of exactly three captured market surfaces under a future typed artifact `bizman.market-observation.v1` (surfaces `retailmarket.city`, `retailprices.group`, `vendors`). It authorizes nothing else and no writes.  
**Record date:** 2026-10-03 (measured by the lead from the private first-party HAR captures).  
**Design reference:** this record.  
**Source capture:** `src.har.bizmania.2026-09-06.01` (`bizmania.ru.har`), identified in `knowledge/sources/captures.json`; see also `docs/research/captures.md`.

## Purpose

This document is the evidence gate record for the S4 market analytics surfaces. All statements below are **measured facts only; no values are invented**. Every entry listed here is a captured first-party request in `src.har.bizmania.2026-09-06.01` with the stated status, MIME type and response identity metadata. Entries that were not measured and listed here are not cited.

If a page or row does not match the exact frozen structure described below, extraction must fail closed: no artifact field is emitted, and nothing is guessed, repaired or inferred from labels, positions or adjacent JSON. This record grants no authority to any other market route, parameter or surface.

## Evidence statuses

This record uses the status vocabulary of `AGENTS.md`:

- `observed` — directly present in the capture (HAR entry / response HTML).
- `documented` — stated by captured BizMania Wiki/help.
- `discovered-reference` — referenced by captured HTML/JavaScript but not observed executing.
- `inferred` — reasoned from evidence but not directly stated.
- `hypothesis` — unverified proposed explanation.
- `verified` — deliberately reproduced by an experiment.
- `contradicted` — evidence conflicts with the statement.
- `deprecated` — historical knowledge no longer expected to apply.

Every structural fact below is `observed` in the listed capture entries unless explicitly labeled otherwise. The UNKNOWN list is explicitly unresolved; none of it may be promoted to a claim without new evidence.

## Authorized surfaces and frozen URLs

Only the following surfaces are admitted. Production admission must use exactly these route and query forms; no other routes, path variants, extra parameters or aliases are authorized.

| Surface | Frozen route + query form | Authority |
| --- | --- | --- |
| `retailmarket.city` | `GET /analitics/retailmarket/?city=<city-id>` | city department table only |
| `retailmarket.city` row link | `GET /analitics/retailmarket/?city=<city-id>&retailgroup=<group-id>` | structural row identity only |
| `retailmarket.city` cross-check | `GET /city/?id=<city-id>&tab=retailmarket` | comparison only, never a source of truth |
| `retailprices.group` | `GET /analitics/retailprices/?retailgroup=<group-id>` | group basket only |
| `vendors` | `GET /analitics/vendors/` | product-link list only |

Nothing in this record authorizes state-changing requests. The POST forms recorded for these routes are explicitly out of scope (see UNKNOWNs).

## Surface A — `retailmarket.city` (city department table)

**observed** in entry 10125: `GET https://bizmania.ru/analitics/retailmarket/?city=25`, status 200, `text/html`, `response_size` 59325, `response_text_sha256` `4a0ea64bb006c0b8897f0a358d28d97c355f2669c8daf8a35098ab4e678b8f50` (`knowledge/http/application-events/part-005.jsonl`).

Frozen structural location, exactly one `table` with class `datatable` whose first row contains `td.tblh1` with `colspan=2`:

- header row has 7 cells (the first two with `colspan=2`);
- 12 data rows for `city=25`;
- each data row has exactly 9 `td`:
  - `c0`/`c1` each carry an `<a>` whose href is exactly `/analitics/retailmarket/?city=25&retailgroup=<G>`, and both cells must agree on `G`;
  - `c2` is bare money text (units `тыс`/`млн`/`млрд` p.);
  - `c3` is a JavaScript dialog link (`category=<G>` cross-check);
  - `c4` is a delta percent;
  - `c5` is a share percent;
  - `c6` is an average markup percent;
  - `c7` is a competition bar image with a `title` only — no numeric field;
  - `c8` is `span.color-bar` with `title` `Уровень цен: <P>%`;
- trailing summary rows (`«Итого:»`, `«Предложение квартир:»`, `«Ипотечный платеж:»`, `«Продажи квартир:»`) have `colspan>=2`, carry no anchors and are skipped.

Cross-check: entry 8054 `GET /city/?id=25&tab=retailmarket`, status 200, shows the same department-table structure.

Entry 10060 `GET /analitics/retailmarket/` with no city query is a **different city-level table** (18 city rows and different columns) and is explicitly **out of the city contract**; it must not be read as a city department table.

The two agreeing `retailgroup` hrefs are the only authorized row identity. If they disagree, are missing or the row shape differs, the row fails closed.

## Surface B — `retailprices.group` (group basket)

**observed** in these entries in `bizmania.ru.har`, status 200, `text/html`:

| Entry | Query | `response_size` | `response_text_sha256` |
| ---: | --- | ---: | --- |
| 9180 | `retailgroup=1` | 38701 | `f10fcfba86a0e1095a654aae5e08d8950d0586261ac1a9ebd454b4c26f6b771d` |
| 9278 | `retailgroup=2` | 38084 | `623a9c68723b0039c84b8daf809ac5d01b8392adf695d9dd004389e1e111e21c` |
| 9371 | `retailgroup=4` | 37408 | `f9f8ad18ff461ea499dbc951e699ec14aa5251846af6eac63233da536a91e9a8` |
| 9459 | `retailgroup=14` | 37346 | `ccf83c7dcaf96b21327b95ed799ee33b884da07ad4e4637307b9c19c7c06e938` |

Entries 9082 and 9547 are captured reloads of the default page (`GET /analitics/retailprices/`, no query); the default no-query form is observed but **not admitted**: production extraction requires the explicit `retailgroup` query and rejects the no-query form. Entry 9645 repeats `retailgroup=14` at the same `response_size` 37346 with a different `response_text_sha256` (prefix `99835d61…`).

Frozen structural location: exactly one `table.datatable`; header row with 4 columns; 24 city data rows followed by one `<tfoot>` summary row (`Среднее:`, `colspan>=2`, no anchor, skipped); each city data row has 5 `td`:

- `c1` contains exactly one `<a href="/city/?id=<C>">` and exactly one `div#basketPrice[i]` / `div#basketQuality[i]` pair (`i` = 0-based city-row index) holding raw decimal numbers; in the capture the two divs are siblings of the anchor inside the same cell, and the parser must not depend on the divs being anchor descendants; the city name is the anchor's own text;
- `c2` is empty or a percent (the player-specific `«Ваша доля»` — presence varies per city);
- `c3`/`c4` are non-empty formatted display text.

Verified structural numbers (raw values, verbatim):

- `retailgroup=1`: first row `city_id=43` raw price `2529.622672`, raw quality `3.121386539935728`; second row `city_id=18` raw `2404.638645` / `3.160650457731355`.
- `retailgroup=4`: first row `city_id=52` raw `95674.922569` / `2.799999952316284`; second row `city_id=25` raw `94802.883471` / `2.815371018032872`. The second row's `c2` `«Ваша доля»` percent is measured but deliberately not recorded here (see Privacy).

The row-bound `div#basketPrice[i]` / `div#basketQuality[i]` pair under the single `/city/?id=<C>` anchor is the only authorized identity and value location. Any deviation (missing/duplicate `div`, index mismatch, extra `a`) fails closed.

## Surface C — `vendors` (product-link list)

**observed** in entry 9733: `GET https://bizmania.ru/analitics/vendors/`, status 200, `text/html`, `response_size` 83436, `response_text_sha256` `530b251fcea07ffc16bb48a59e3de5766978a4fb1e8f3aa972373a73bd774629` (`knowledge/http/application-events/part-005.jsonl`).

Verified: exactly one table contains product anchors; inside it there are 16 `h3` group sections (17 `h3` appear page-wide, but the extra one belongs to an unrelated block outside the catalog table); 273 anchors whose href is exactly `/analitics/vendors/?product=<N>`; all 273 product ids are distinct; no price or quality values are visible in the DOM (only image `title`/`alt` labels). The surface is therefore deferred to a product-link list; no price, quality or identity field beyond the product-link form is authorized here.

## HTTP knowledge cross-reference

- `knowledge/http/endpoints/part-000.json` records `/analitics/retailmarket/` count 2 (query key `city`), `/analitics/retailprices/` count 7 (query key `retailgroup`), `/analitics/vendors/` count 1.
- `knowledge/http/forms/part-000.json` records GET and POST forms for `retailmarket`/`retailprices` (for example GET `retailmarket` with `city`/`sort`/`k`; POST with `p`/`sort`/`$post`). The POST semantics of these analytics pages are **UNCLASSIFIED** and out of scope; no state-changing request will be made.
- `knowledge/http/application-events` contains 10 market records.

## Explicit UNKNOWNs (unresolved)

1. **Pagination/completeness** of any captured list: a `p` parameter appears only in forms, no captured market URL uses pagination, and a missing row is never deletion.
2. **Server-side rendering stability** for repeated loads: entry 9645 vs 9459 have the same `response_size` (37346) but different `response_text_sha256`.
3. Whether `«Ваша доля»` (`c2` in the basket table) appears for all cities or only the player's own city.
4. Whether the department/group set is complete (12 rows observed for `city=25`).
5. **POST semantics** of the analytics forms.
6. **Vendor prices/qualities** — not present in the DOM.

## Privacy

The department table and basket pages are rendered for an authenticated session. Only structural fields are proposed for extraction: route and query form, row shape, row-bound link identity, raw basket decimals bound to the frozen `div` pair, and the agreed `retailgroup` value. No raw HTML, cookies, tokens or hidden form values participate in identity or are stored. `«Ваша доля»` is player-specific; this record deliberately contains no per-city share values, and any measured per-city share content is omitted from this public document.

## Authorization boundary

The PASS above authorizes only the three read-only surfaces `retailmarket.city`, `retailprices.group` and `vendors` under a future typed artifact `bizman.market-observation.v1`. Production admission must use the exact frozen route and query forms listed above; unsupported or ambiguous structures fail closed; nothing here authorizes writes, POST calls or any other surface. Any later structural change requires a new gate record rather than a revision of observed facts.
