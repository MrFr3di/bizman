# P4-C C0 — local runbook (authorized real-evidence run)

**Status:** operational checklist for the single locally authorized C0 session.
**Design:** `docs/superpowers/specs/2026-09-30-p4c-product-identity-design.md`
**Readiness audit:** `docs/research/p4c-pre-c0-readiness.md`
**Decision record:** `docs/research/p4c-product-evidence.md`

This runbook contains no private data. It is the checklist to execute before
filling the decision matrix in `docs/research/p4c-product-evidence.md`.

## 0. Prerequisites

- [ ] `uv` pinned to the repository-required version `0.12.10`
      (the running `uv` must report `0.12.10`; the repo enforces `required-version ==0.12.10`).
- [ ] `uv sync --locked` passes in the repository checkout.
- [ ] A dedicated Chrome profile logged in to `bizmania.ru` with a real game session
      (you must own the units you inspect).
- [ ] Browser remote debugging enabled if you capture bodies through CDP
      (`--remote-debugging-port=9222` on a dedicated profile) — otherwise DevTools
      manual response saving is sufficient.

## 1. Local capture area (never inside the repository)

```powershell
$C0 = Join-Path $env:TEMP "bizman-p4c-c0"
New-Item -ItemType Directory -Force $C0 | Out-Null
```

Raw authenticated bodies stay in `$C0` only. Do not commit them, paste them into
issues, CI logs or PRs, and do not move them under `D:\Repos\BizMan`.

## 2. Capture cases

Collect each case from the authorized logged-in Chrome session and save the
HTTP response body (DevTools -> Network -> select the document request ->
Response -> save as `<case>.html` in `$C0`).

| Case | URL to open | Save as | Requirement |
| --- | --- | --- | --- |
| A1 | `https://bizmania.ru/units/shop/?id=13548&tab=goods` | `A1.html` | at least 2 visible product rows |
| A2 | same shop after reload and, if possible, row reorder | `A2.html` | same shop, second observation |
| B1 | `https://bizmania.ru/units/shop/?id=33670&tab=goods` | `B1.html` | second unit, same surface |
| P1 | paginated goods page, if pagination exists | `P1.html` | page 2 or explicit no-pagination finding |
| S1 | `https://bizmania.ru/units/shop/?id=13548&tab=supply` | `S1.html` | comparison surface only; not auto-promoted |

Alternative target units from the historical census: `13443`, `13507`, `13534`,
`13576`, `17181`, `17184`, `17245`, `26230`, `33676`.

Record separately:

- [ ] Chrome product/version and (if CDP) protocol version metadata;
- [ ] exact unit id and case label per capture;
- [ ] the exact structural relation observed in DevTools between each candidate
      product href and its row;
- [ ] whether hidden/inactive markup contains competing product-like ids;
- [ ] whether A1/A2 preserve identity under reload/reorder;
- [ ] whether pagination exists and what one page omission means.

## 3. Probe runs

```powershell
uv run bizman probe-product-evidence `
  --repo-root "$PWD" `
  --source-url "https://bizmania.ru/units/shop/?id=<UNIT>&tab=goods" `
  --body-file "$C0\<CASE>.html"
```

Exit code 0 means only that the bounded inspection completed; it is never a
C0 PASS signal. The probe reports candidate numeric ids only.

Run once per case: A1, A2, B1, P1 (if it exists). S1 is rejected by the goods
probe by design; its bodies are for manual structural comparison only.

## 4. Decision

Fill the matrix in `docs/research/p4c-product-evidence.md` from the recorded
observations. PASS requires one exact row-bound field/location that:

1. identifies the corresponding product on at least two units;
2. remains stable across harmless reload/order change;
3. can be extracted without persisting broad hidden/form/script state;
4. has understood duplicate/ambiguity behavior;
5. has explicit pagination/completeness semantics.

NO-GO conditions are listed in the decision record; any of them stops P4-C
before schema v3 and the deliverable becomes a documented evidence gap.

## 5. After the run

- [ ] Delete or archive `$C0` locally (raw bodies are never needed again).
- [ ] Record the decision (PASS / NO-GO / PENDING with reasons) in
      `docs/research/p4c-product-evidence.md`.
- [ ] If PASS: proceed to the P4-C implementation plan (ProductEvidenceV1
      capture, reducer, schema v3) — research contract first, per the pre-C0
      audit ordering.
