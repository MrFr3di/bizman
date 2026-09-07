# BizMania Wiki corpus

The supplied Wiki capture contains 87 full normalized topics. The captured navigation references 89 topics total, so two navigation topics were not present as captured pages.

## Data

- `knowledge/wiki/topics/index.json` — partition manifest for all 87 captured topics.
- `knowledge/wiki/navigation.json` — categories, 89 navigation topics, 87 captured topics and the two missing-from-capture topics.

Each `knowledge/wiki/topics/part-*.jsonl` record retains the source topic/title, normalized text and capture evidence produced from `bizmaniaFAQ.ru.har`.

Use the Wiki corpus for `documented` mechanics. Do not automatically treat Wiki statements as proof of current runtime behavior: runtime claims should be cross-checked against `knowledge/http/*`, `knowledge/pages/*` or a controlled experiment and upgraded to `verified` only when reproduced.
