# P4-C C0 — Product Identity Evidence Gate

**Status:** IN PROGRESS / NO AUTHORITY GRANTED  
**Date:** 2026-09-30  
**Design:** `docs/superpowers/specs/2026-09-30-p4c-product-identity-design.md`  
**Pre-C0 audit:** `docs/research/p4c-pre-c0-readiness.md`

## Purpose

This document records the reproducible C0 decision for product identity. Until the matrix below is completed from locally authorized real first-party evidence, P4-C production capture and Current State schema v3 are forbidden.

Historical `knowledge/pages` demonstrates that product-facing pages exist, but its normalized visible text cannot prove a stable row-bound numeric product identifier.

## Research tooling

Use the research-only command against a local response-body file:

```text
bizman probe-product-evidence \
  --repo-root <repo> \
  --source-url 'https://bizmania.ru/units/shop/?id=<unit>&tab=goods' \
  --body-file <local-response.html>
```

The command:
- accepts only the exact approved shop/goods route;
- rejects control/space-normalized URL variants before URL parsing;
- reads at most 4 MiB locally;
- does not copy the body into BizManData or CAS;
- prints only bounded structural candidate metadata;
- currently recognizes only relative `/products/?id=<positive-int64>` hrefs as research candidates;
- ignores labels, hidden form values, DOM id/name and arbitrary data attributes;
- fails closed on duplicate attribute ambiguity or candidate-count overflow rather than silently truncating.

A candidate is **not** yet an authoritative product ID. A zero exit code means only that bounded research inspection completed. C0 must prove row binding, stability and completeness separately.

## Required real-evidence matrix

| Probe | Required observation | Result |
| --- | --- | --- |
| Shop A / goods | >=2 product rows with row-bound stable numeric IDs | PENDING |
| Shop A reload/reorder | same IDs survive ordering/reload | PENDING |
| Shop B / goods | same structural identity contract on another unit | PENDING |
| Pagination, if present | omission/completeness semantics measured | PENDING |
| Supply comparison | determine whether it is a distinct semantic surface | PENDING |
| Privacy | identity extraction needs no token/cookie/arbitrary hidden value | PENDING |
| Browser/CDP | Chrome product/version + protocol metadata recorded | PENDING |

## PASS conditions

PASS requires all of the following:
1. the same exact structural field identifies products on at least two units;
2. the ID is attached to the corresponding product row;
3. row order and visible label are not identity;
4. extraction does not require persisting raw HTML or arbitrary secret-bearing fields;
5. ambiguity/duplicate behavior is understood;
6. pagination/completeness is explicitly classified.

## NO-GO conditions

Any of these keeps P4-C schema v3 blocked:
- only display names/row positions are available;
- candidate IDs cannot be proven to belong to the row;
- IDs change under harmless reorder/reload;
- extraction requires broad hidden/form/script state;
- different units use incompatible identity semantics without a separately versioned contract;
- completeness/removal is inferred rather than observed.

## Current decision

**PENDING.** Research infrastructure exists, but no real authenticated body has been supplied to the C0 probe in this repository workflow. Therefore no product identity has yet been promoted to authoritative Current State evidence and schema v3 remains blocked.
