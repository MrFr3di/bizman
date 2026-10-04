# P4-D D0 — Supply Link / Order Evidence Gate

**Status:** PENDING — research tooling exists, but no production supply association is authorized.  
**Record date:** 2026-10-03.  
**Parent:** issue #47.  
**Predecessor:** P4-C product identity / unit-economics completion, issue #40.

## Purpose

P4-C proved the product identity contract for the `shop.goods` surface. That
decision does not authorize supply semantics. P4-D begins with a separate
go/no-go evidence gate for:

```text
GET /units/shop/?id=<unit>&tab=supply
```

The goal is to determine whether supplier/order identity and row association can
be observed safely and reproducibly from first-party read-only evidence before
adding any production collector artifact, Current State table, reducer or schema
version.

## Known evidence before D0

The P4-C evidence work observed one local `tab=supply` comparison for unit
33676 and classified it as a distinct semantic surface. It reused product
numeric IDs, but inherited no authority from `shop.goods`.

The separately observed product-card anomaly
`/units/shop/?id=17245&tab=goods&product=418` exposed form names including
`maxPrice`, `minQuality`, `targetStockQuantity`, `vendor[0]`,
`vendorQuantity[0]` and `vendorPrice[0]`. Those names are useful research
leads only. The product-card surface is not the supply surface, and neither a
field name nor a hidden value proves supplier identity, active order state or
row membership.

## Research-only command

Use a locally authorized response body:

```text
bizman probe-supply-evidence \
  --repo-root <repo> \
  --source-url 'https://bizmania.ru/units/shop/?id=<unit>&tab=supply' \
  --body-file <local-response.html>
```

The command:

- accepts only the exact HTTPS first-party supply route with exactly one
  positive decimal `id` and `tab=supply`;
- reads at most 4 MiB locally;
- never writes the input body to BizManData, CAS or Git;
- never emits visible text, input values, raw HTML, headers, cookies or full
  arbitrary URLs;
- reports only bounded structural metadata:
  - relative link path;
  - query-key names;
  - positive signed-64-bit numeric candidates for the narrow keys
    `id`, `product`, `vendor`, `unit`, `supplier`;
  - whether the link is structurally inside a row/form;
  - ephemeral `row_slot` / `form_slot` values used only to group candidates
    within one probe result;
  - input field base name and optional canonical array index;
- treats an `input type=hidden` like any other structural input: its name may
  be reported, its value is never retained;
- ignores hidden/inactive subtrees;
- fails closed on duplicate HTML attributes/query keys, malformed or
  percent-encoded/path-relative first-party candidates, nested/incomplete
  structural containers, non-canonical array indexes or candidate overflow.

Output schema: `bizman.supply-probe.v1`.

The output is research evidence only. `row_slot` and `form_slot` are local
grouping coordinates, not stable identities and never survive as domain keys.
A structural candidate is not a supplier ID, order ID or authoritative
association until this gate records PASS.

## D0 real-evidence matrix

Before PASS, collect locally authorized read-only evidence for at least:

| Case | Required observation | Status |
| --- | --- | --- |
| S1 — unit A supply | at least two supply/product rows with structural candidates | PENDING |
| S2 — same unit reload | identity and row association survive harmless reload | PENDING |
| S3 — unit B supply | same structural contract on a second unit | PENDING |
| S4 — product with multiple suppliers, if available | supplier candidates remain attached to the correct product/order row | PENDING |
| S5 — empty/partial supply state | determine whether omission means none, pagination, inactive row or unknown | PENDING |
| Privacy | supplier identity can be proven without persisting arbitrary form values/raw HTML | PENDING |
| Pagination/completeness | page completeness semantics explicitly classified | PENDING |

Raw bodies stay local. Commit only sanitized structural conclusions.

## PASS conditions

D0 may PASS only if all of the following are supported by real evidence:

1. one exact structural field/location provides a stable supplier or supply-link
   identity on at least two units;
2. the identity is demonstrably attached to the correct unit/product/order row,
   rather than inferred from visible labels or row order;
3. repeated harmless reads preserve the identity;
4. ambiguity/duplicate behavior is understood and fail-closed;
5. the minimum numeric supply fields intended for production have a proven
   source and unit/meaning; field names alone are insufficient;
6. extraction requires no raw HTML, arbitrary hidden values, auth material or
   user text in durable evidence;
7. pagination/completeness and omission semantics are explicit;
8. a reviewer can distinguish supplier identity, requested quantity/price and
   observed current order state without relying on request intent.

## NO-GO conditions

Stop production work if any applies:

- only supplier/product labels, row ordinals or unstable DOM positions are
  available;
- supplier identity is available only as an unproven hidden/form value;
- one numeric value could plausibly mean supplier, unit, product or row index
  and cannot be disambiguated;
- rows cannot be deterministically bound to the corresponding product;
- omission/completeness cannot be classified but the proposed reducer would
  treat missing rows as deletion;
- extraction requires broad form/script state or secret-bearing values;
- different units expose incompatible semantics without separately versioned
  contracts.

## Production work forbidden while PENDING

Until D0 PASS, do not:

- widen passive response-body capture to `tab=supply`;
- define a production `SupplyEvidenceV1` artifact;
- add supplier/order Current State tables or schema v5;
- derive current supplier links from POST request intent;
- interpret `vendor[]`, `vendorQuantity[]` or `vendorPrice[]` names/values
  as authoritative facts;
- expose supply writes through Core, MCP or Telegram.

## Next allowed action

Run the research probe against locally authorized S1/S2/S3 captures and record
only structural conclusions. If identity/association is proven, freeze the
minimal production artifact contract before implementing collection/replay.
