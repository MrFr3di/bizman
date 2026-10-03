# P4-C C0 — offline evidence runbook (executed 2026-10-03)

**Status:** executed; C0 result **PASS** recorded in `docs/research/p4c-product-evidence.md`.  
**Design:** `docs/superpowers/specs/2026-09-30-p4c-product-identity-design.md` (amendment §19)  
**Readiness audit:** `docs/research/p4c-pre-c0-readiness.md` (pre-C0 state)  
**Decision record:** `docs/research/p4c-product-evidence.md`

This runbook records the offline C0 run actually executed on 2026-10-03. The structural part
needs no live logged-in game session: it uses the private HAR corpus plus response bodies
already saved under the ignored `.work/` directory. Raw bodies never enter Git.

## 0. Environment

- `uv` **0.12.22** was used for the executed run. The local, ignored `.work/uv.toml`
  (`required-version = "==0.12.22"`) was supplied as the uv config override; `pyproject.toml`
  was not changed.
- Dependencies stayed in locked mode (`uv sync --locked`).
- No credentials, cookies, tokens, browser profiles or live game session are needed for the
  structural extraction described here.

## 1. Sources

Private and never committed:

- the three HAR captures whose counts, period and SHA-256 are recorded in
  `docs/research/captures.md`:
  - `bizmania.ru.har`;
  - `bizmania1.ru.har`;
  - `bizmaniaFAQ.ru.har`.
- the local live response bodies saved under ignored `.work/`, recorded in
  `.work/capture-manifest.json`:
  - `src.c0.20261003.a1` — unit 33670, `tab=goods` (`shop-a-1.html`);
  - `src.c0.20261003.a2` — unit 33670, `tab=goods`, reload (`shop-a-2.html`);
  - `src.c0.20261003.b1` — unit 33676, `tab=goods` (`shop-b-1.html`);
  - `src.c0.20261003.s1` — unit 33676, `tab=supply` (comparison only);
  - `src.c0.20261003.f1` — unit 33676, product-card comparison (outside the goods contract).

## 2. HAR corpus scan (offline)

For every first-party HAR entry whose document URL is exactly the shop goods surface
(`/units/shop/?id=<unit>&tab=goods`), inspect the response body for the frozen selector and
record only structural facts: goods-table presence, row count, numeric `product=N` values,
pagination-like query keys (`p`, `page`, `offset`, `start`, `limit`) and the UA string. Never
export product labels, prices, hidden values, cookies or headers.

Result (2026-10-03): 21 `tab=goods` entries, 18 complete goods tables, 12 units, 61 distinct
numeric product ids. The per-capture matrix, the reload comparison, the e5307 anomaly and the
explicit UNKNOWNs are in the evidence gate `docs/research/p4c-product-evidence.md`.

## 3. Probe runs (offline, research-only)

The research probe reads one local body transiently, never copies it into BizManData or CAS and
prints only bounded structural candidate metadata. Run it once per saved goods body, with the
source URL exactly `{id, tab}` (a URL carrying `product=...` is rejected by design):

```powershell
uv run bizman probe-product-evidence `
  --repo-root "$PWD" `
  --source-url "https://bizmania.ru/units/shop/?id=33670&tab=goods" `
  --body-file ".work/shop-a-1.html"
```

Run under the uv 0.12.22 config override from `.work/uv.toml`. Repeat for
`.work/shop-a-2.html` (unit 33670) and `.work/shop-b-1.html` (unit 33676). Notes:

- the frozen row-bound goods selector is recognized as a **non-authoritative** candidate
  extractor; exit code 0 only means the bounded inspection completed and is never a PASS signal;
- `tab=supply` is not accepted by the goods probe; `shop-b-supply-1.html` was compared manually
  only and inherits no trust;
- the product-card body `shop-b-product-416.html` is outside the source route contract and is
  likewise comparison-only.

## 4. Results and decision

The filled evidence matrix, the dated PASS decision, the classified e5307 anomaly and the
remaining UNKNOWNs (server row reorder, completeness/pagination, deletion) are recorded in
`docs/research/p4c-product-evidence.md`. The frozen contract and field map are in the design
amendment `docs/superpowers/specs/2026-09-30-p4c-product-identity-design.md` §19.

## 5. Privacy rules (unchanged)

- raw HAR files and response bodies never enter Git (`.har` and `.work/` are ignored);
- no cookies, tokens, Authorization headers, browser profiles or credentials are stored or
  required in repository files or in this runbook;
- durable documentation keeps only structural numeric metadata and capture identities;
- probe output is bounded structural metadata, never raw markup or labels.
