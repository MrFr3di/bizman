# P6 — HAR → Knowledge Regeneration Spike (S6)

**Status:** Spike complete; acceptance as stated 5/8 — blocked for routes/forms/pages-7151, with wiki related_topics as a partial deviation.  
**Record date:** 2026-10-03.  
**Design/code:** `src/bizman/ingest/` (`har.py`, `datasets.py`, `__main__.py`); merged.  
**Evidence base:** the three private captures held locally as untracked copies — `bizmania.ru.har`, `bizmania1.ru.har`, `bizmaniaFAQ.ru.har`; canonical source identifiers are recorded in `knowledge/sources/captures.json`.

## Purpose

This report is the durable record of the S6 "HAR → knowledge ingest" regeneration spike. The spike implemented deterministic dataset generation in `src/bizman/ingest/` and compared the regenerated records against the committed knowledge corpus.

The originally intended acceptance was: **regenerate the 8 partitioned HAR-derived datasets with identical record multisets**. The honest measured result is 5/8 exact and 3 blocked. This report records the verified facts and the blockers; it does not silently redefine acceptance.

## Method

**observed / verified:** the comparison was produced by the CLI `python -m bizman.ingest --har ... --out ... --compare knowledge`, run locally over the three private captures listed above. The `--compare` pass performs a multiset comparison over canonical JSON records and reports generated-only and committed-only counts (`observed` in `src/bizman/ingest/__main__.py`). First-party filtering uses the URL host `bizmania.ru`; 16202 first-party entries were identified across the three captures (`observed` / `verified`).

- No committed corpus file was modified by the spike; all generated output was written to an untracked local directory.
- `products` was excluded from the 8 by design: its manifest states it was derived from the Wiki product directory plus observed live game IDs, not from HAR alone (`observed` in `knowledge/domain/products/index.json`).

## Verified regeneration rules

- `application-events`: `response_text_sha256` is SHA-256 over the exact `content.text` string encoded UTF-8. For base64 bodies this hashes the encoded text, not the decoded bytes — `verified` against committed entries 10060, 10125, 9180, 9733 and base64 entries 906/907/908/909/7825, 0/555 mismatches. `response_size` = `content.size` when present, else UTF-8 byte length of the text. `query` keeps the first value per key with `parse_qsl` decoding (`observed` in `har.py`; `verified` against the corpus).
- `endpoints`: aggregation of application events by normalized `path_pattern` (`observed` in `datasets.py`; `verified` 68/68).
- `assets`: static first-party entries aggregated by path with counts, captures, mime types and statuses (`observed`; `verified` 914/914).
- `json-responses`: records sorted by path; `data` = `json.loads(text)`, falling back to the raw text string on invalid JSON, e.g. entry 7889 trailing comma (`observed`; `verified` 36/36).
- `game-html-pages`: page text rule — text nodes of the `#content` subtree only, script/style/noscript/textarea/iframe skipped, chunks stripped and joined with "\n"; title = raw `<title>` text (`observed` in `datasets.py`; `verified` 179/180 as full records).
- `wiki-topics`: unique captured Wiki topics; article text from the `font.text` body; records sorted by topic (`observed` in `datasets.py`; content fields `verified` 87/87).

## Per-dataset matrix

| Dataset | Generated | Committed | Result (acceptance as stated) |
| --- | ---: | ---: | --- |
| `application-events` | 555 | 555 | **exact** — identical including record order (`verified`) |
| `endpoints` | 68 | 68 | **exact** (`verified`) |
| `assets` | 914 | 914 | **exact** (`verified`) |
| `json-responses` | 36 | 36 | **exact** (`verified`) |
| `wiki-topics` | 87 | 87 | **exact on content fields** — `topic`/`source`/`text`/`html_sha256` 87/87 (`verified`); `related_topics` partial deviation for 17/87 (see Blockers) |
| `game-html-pages` | 180 | 180 | **blocked** — 179/180 exact (title + text + `html_sha256`); entry 7151 `contradicted` |
| `routes` | 843 | 761 | **blocked** — 299 generated-only and 217 committed-only records (`observed`) |
| `forms` | 129 | 87 | **blocked** — committed `form_id` scheme not recoverable (`verified`) |

Acceptance as stated: **5/8 exact**. `wiki-topics` counts as a pass on its deterministic content fields with the `related_topics` deviation recorded openly; `game-html-pages`, `routes` and `forms` are blocked.

## Blockers

### `routes` — committed count semantics are not uniquely reconstructable

- Generated 843 vs committed 761: 299 generated-only and 217 committed-only records (`observed` in the comparison).
- Routes are declared as discovered in HTML/JavaScript, not a pure HAR aggregation (`observed` in `knowledge/catalog.json`); the committed count semantics (raw occurrences vs document frequency vs distinct concrete routes per entry) cannot be uniquely reconstructed.
- The reconstruction over-collects footer/menu links (e.g. `/about/*`) and under-collects some text-scraped counts (`observed` in the generated-only and committed-only sets).
- Consequence: blocked until the original aggregation semantics are recovered or a corpus correction decision defines them. Neither is in this spike's scope.

### `forms` — committed `form_id` values are opaque

- Generated 129 vs committed 87 (`observed`).
- Parsing (method/action/named inputs/selects/textareas, first-seen `observed_on`) is deterministic (`observed` in `datasets.py`), but every committed `form_id` is an opaque 16-hex value whose preimage was not recovered after documented attempts over ~1.5k canonicalizations (MD5/SHA-1/SHA-256/BLAKE2 truncations of form name, raw `outerHTML`, whitespace-normalized variants) (`verified`: the attempts did not recover it).
- Generated ids use `SHA-256(signature)[:16]`, so forms cannot match by construction (`observed` in `datasets.py`).
- Consequence: blocked until the original id scheme is recovered; the count deviation (129 vs 87) is a further observed difference and is not explained by this report.

### `game-html-pages` — entry 7151 contradicts its own committed hash

- Generated 180; 179/180 exact on title + text + `html_sha256` (`verified`).
- One `contradicted` record: entry 7151 — the committed record's text contains "-64 251" in ten places where the HAR body contains "-62 796" (independently re-verified: the committed text has "-64 251" and not "-62 796"; the HAR body has "-62 796" and not "-64 251") (`observed`).
- The committed `html_sha256` equals SHA-256 of the HAR body, so the committed text is internally inconsistent with its own hash (`contradicted` by the body/hash pair).
- Consequence: blocked pending a corpus correction decision; this report does not propose rewriting committed knowledge.

### `wiki-topics` — `related_topics` partial deviation (not counted as blocked)

- Generated 87 vs committed 87; `topic`/`source`/`text`/`html_sha256` exact for all 87 (`verified`).
- `related_topics` differ for 17 of 87: the committed record has `[]` where the page's own "Читайте также" block names a different topic (`observed` in the captured HTML; the committed value deviates).
- The dropped set is not separable by category, capture order, alphabet, distance or taxonomy membership (`observed` — none of the tested separators partitions it).
- The 17 topics: Биржа акций, Бонусные дотации в городах, Виды предприятий, Земледельческая ферма, Износ оборудования, Ипотека, Квесты, Корпоративные квесты, Кредитование населения, Магазин, Налоги, Опыт, Повстанцы, Резервный фонд, Справочник продуктов, Школа БМ, Штаб-квартира: полезные советы.
- Cause is UNKNOWN; recorded as a partial deviation rather than a silent pass.

## What this means for the acceptance

- 5 datasets regenerate exactly: `application-events`, `endpoints`, `assets`, `json-responses`, and `wiki-topics` on its deterministic content fields (with the `related_topics` partial deviation recorded above). 3 datasets cannot be reproduced as full record multisets: `routes` and `forms` require the lost original tooling (or an explicit decision defining the original semantics), and `game-html-pages` requires a corpus correction decision for entry 7151.
- The committable package is already merged: `src/bizman/ingest/` reproduces the exact datasets and fails closed; this spike adds no code changes to it.
- The decision to correct any committed knowledge is out of scope for this task and is not proposed here. Acceptance remains as originally stated; 5/8 is the honest measured outcome.

## Explicit UNKNOWNs

- `form_id` preimage/canonicalization: UNKNOWN after the documented ~1.5k attempts.
- Committed `routes` count semantics (raw occurrences vs document frequency vs distinct concrete routes per entry): UNKNOWN; not uniquely reconstructable from the committed records and the HAR corpus.
- Cause of the entry 7151 text/hash contradiction: UNKNOWN; only the contradiction itself is observed.
- Why the 17 wiki topics carry empty committed `related_topics`: UNKNOWN; no tested separator explains the dropped set.
- Whether or how the committed corpus should be corrected: undecided and out of scope.

## Privacy and data handling

- Raw HAR/CDP streams are evidence, not repository content (AGENTS.md). The three captures were used as untracked local copies and are not committed.
- No cookies, Authorization headers, browser profiles, storage state, passwords, `.env`, or operational data are reproduced here.
- All generated output was written to an untracked local directory; no committed corpus file was modified by the spike.
- This report records only bounded structural facts (counts, entry indices, rule definitions); it contains no private account values and no raw capture content beyond those facts.
