# Архитектура хранения

## В Git

Репозиторий содержит код, JSON Schema, документацию и структурированные curated/derived knowledge-наборы. Здесь допустимы только небольшие публично-безопасные текстовые evidence-наборы, необходимые для поиска, тестов и воспроизводимости.

`RuntimeContract` Change Detector не хранится как отдельная авторитетная база: он детерминированно компилируется из committed curated knowledge + redaction semantics. Git остаётся источником версии интерпретации, а live evidence — локальным immutable источником наблюдений.

## Вне Git

Исходные HAR, live collector evidence, рабочие SQLite/Parquet, cookies, browser storage, CAS и Promotion Bundles хранятся локально вне worktree. Текущая operational layout:

```text
BizManData/
  sessions/
    <session-uuidv7>/manifest.json
  events/
    <date>/<session-uuidv7>.jsonl
  artifacts/
    sha256/<prefix>/<digest>
  detector/
    state.sqlite3
    state.sqlite3-wal          # transient when applicable
    state.sqlite3-shm          # transient when applicable
  promotions/
    <analysis-profile-prefix>/<session-uuidv7>/promotion.<sha256>.json
  parquet/                     # future historical projection
```

Ни `BizManData`, ни detector SQLite/outbox, ни materialized Promotion Bundles не являются Git-артефактами и не должны автоматически коммититься.

## Слои

1. **RAW** — первичный capture, если он сохраняется отдельно. Не коммитится.
2. **EVIDENCE** — append-only sanitized collector JSONL + content-addressed CAS. Это локальный immutable source of truth для runtime observations. Корреляция не переписывает request/action события: `correlation.action_http` добавляется отдельным immutable event.
3. **CURATED CONTRACT INPUT** — committed knowledge, схемы и redaction policy, из которых детерминированно строится `RuntimeContract`.
4. **DETECTOR DERIVATIONS** — SQLite checkpoints/change history/outbox и schema-valid Promotion Bundles. Они rebuildable из curated contract input + immutable evidence.
5. **DOMAIN / ANALYTICS** — последующие current-state и historical projections. Они не должны становиться более авторитетными, чем исходное evidence/provenance.

## Change Detector storage semantics

`analysis_profile_sha256` связывает один replay interpretation с:

- `baseline_sha256` compiled RuntimeContract;
- contract/normalization/extraction versions;
- redaction-policy semantic hash;
- отсортированными rule IDs + versions.

Изменение любой из этих интерпретационных размерностей создаёт новый analysis profile вместо молчаливого переиспользования старых checkpoints.

`BizManData/detector/state.sqlite3` — локальная rebuildable БД detector v1. Она использует SQLite STRICT/WAL, `trusted_schema=OFF`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`, application ID `0x424D4431` и `user_version=1`. Запись checkpoint/findings/pending outbox выполняется одной explicit `BEGIN IMMEDIATE` transaction.

Файловая публикация Promotion Bundle намеренно отделена от этой транзакции. После commit materializer:

1. читает pending outbox;
2. повторно проверяет canonical JSON/hash/schema/privacy;
3. атомарно materializes файл;
4. помечает outbox row как `materialized`.

Если процесс падает между пунктами 2–4, следующий normal run сначала восстанавливает pending outbox. Существующий файл принимается только при точном совпадении bytes. Это исключает неатомарный DB/file dual write.

## Evidence integrity boundary

Detector читает только finalized `completed`/`cancelled` sessions для анализа. Manifest и events проходят Draft 2020-12 validation; event sequence должна быть contiguous с нуля, event IDs уникальны, JSONL хешируется по фактически прочитанным bytes. CAS ref вида `sha256:<digest>` проверяется по фактическому payload digest перед extraction.

Все evidence roots (`sessions/`, `events/`, `artifacts/sha256/`) обязаны после filesystem resolution оставаться внутри configured `BizManData`; path traversal и symlink escape fail closed.

Отсутствующий `request_body_ref` означает **unknown evidence**, а не пустой body. Поэтому structural extraction сохраняет `body_keys=None`, и операция становится `INDETERMINATE`, если решение зависит от неизвестной body structure.

## Форматы

- HAR: только первичное доказательство вне Git.
- JSONL: immutable sanitized collector events.
- CAS bytes: sanitized request/protocol artifacts, addressed by SHA-256.
- JSON: committed catalogs/indexes и local canonical Promotion Bundles.
- SQLite: detector operational checkpoints/change identity/outbox; rebuildable, вне Git.
- Parquet: будущая historical analytics projection, вне Git.

## Семантика событий и связей

`dom.action` и `http.request` являются независимыми наблюдениями. Детерминированный correlator не добавляет ссылку задним числом внутрь этих событий: он выпускает отдельное `correlation.action_http` с `action_event_id`, `network_event_id`, score/status и явным набором сигналов. Это сохраняет append-only provenance и позволяет позже пересчитать или заменить алгоритм корреляции, не меняя исходные evidence-события.

Эвристическая корреляция имеет `confidence=inferred`; статус `exact` для неё не используется. Значения полей формы, текст/HTML элементов, cookies/storage/clipboard не являются частью action event model и не допускаются в Promotion Bundle.

## Почему не хранить raw HAR и operational state в Git

Даже private Git не является секрет-хранилищем. HAR может содержать session/auth данные и много дубликатов; operational БД/Parquet/Promotion Bundles могут содержать локальную историю и быстро устаревать. SHA-256 provenance связывает производные знания с исходным evidence без помещения raw/runtime state в историю репозитория.
