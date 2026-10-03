# Индекс проекта

## Точки входа

- `README.md` — назначение репозитория, состав корпуса и правила работы.
- `knowledge/catalog.json` — главный машинный каталог с актуальными путями и counts.
- `AGENTS.md` — инструкции для Codex/агентов.
- `SECURITY.md` — правила работы с приватными HAR/session/auth данными.

## Архитектура

- `docs/architecture/storage.md` — разделение raw/derived и правила хранения.
- `docs/architecture/provenance.md` — provenance, evidence и уровни уверенности.
- `docs/research/captures.md` — сведения об исходных захватах.
- `knowledge/sources/captures.json` — точные SHA-256 и capture-level метаданные HAR.
- `knowledge/sources/promoted-sessions.json` — machine-readable provenance identity исторических promoted collector sessions.
- `knowledge/sources/webcopy-bizmania.2026-10-03.01.json` — манифест внешнего офлайн-снапшота публичных страниц; `docs/research/webcopy-2026-10-03.md` — методика, провенанс и границы извлечения.
- `bizman.core.trace_evidence` / MCP `evidence.trace` — bounded provenance resolution без доступа к raw HAR/JSONL; webcopy-источник резолвится по `entry-N` до source-level identity и SHA-256 origin index; записи сохраняют `source.uri`/`html_sha256`.
- `docs/ROADMAP.md` — текущий delivery stage и границы следующих этапов; P3 read-only MCP завершён, текущий этап — P4 Replayable Current State.

## Current State

- `src/bizman/current/` — deterministic replay model/store over finalized immutable evidence.
- `BizManData/state/current.sqlite3` — external rebuildable Current State database; never source of truth and never committed.
- P4-A establishes projection/profile identity, replay ledger, `input_fingerprint`, `state_fingerprint`, ready/stale state and atomic rebuild semantics.
- `bizman.core.rebuild_current_state` — path-free Core rebuild use case; callers do not provide a DB path or arbitrary profile hash.
- Delete/replay equivalence is a tested contract. P4-B companies/units projection is complete; P4-C's C0 product-identity evidence gate passed 2026-10-03 (`docs/research/p4c-product-evidence.md`), and the next slice is Current State schema v3 unit economics (runbook: `docs/research/p4c-c0-runbook.md`).
- `bizman.core` exposes bounded path-free Current State reads: `current_status`, `list_current_companies`, `list_current_units`, `list_current_products`. List cursors are operation-scoped to the request filter and bound to `state_fingerprint`.
- CLI surface: `bizman current rebuild|status|companies|units|products`; MCP surface: read-only `current.status`, `current.companies`, `current.units`, `current.products`.

## Agent Index / read model

- `src/bizman/readmodel/` — детерминированная проекция curated knowledge и runtime intelligence.
- `BizManData/index/agent-index.sqlite3` — внешний rebuildable SQLite read model; не является source of truth.
- Текущая curated-проекция индексирует 632 records: исторический P2 baseline плюс 42 новых endpoint identities и документированное product enrichment без создания дубликатов товаров; P2-C добавляет verified `session_summary` и profile-scoped `change_index`.
- P2-D предоставляет bounded path-free read API через `bizman.core`: knowledge resolve/search/get, session list/get и profile-scoped change list/get.
- `docs/benchmarks/p2e-readmodel-baseline-2026-09-28.md` — durable P2-E baseline: v1/v2/v3 retrieval quality, cold/warm latency, rebuild scaling, result budgets and query-plan decisions.
- `docs/benchmarks/p3e-mcp-baseline-2026-09-29.md` — durable P3-E baseline: retrieval parity, action provenance, call budgets, bounded schemas/results, sanitized errors and installed stdio proof.
- `src/bizman/mcp/` — завершённый read-only stdio adapter из 14 Core-backed tools.
- `src/bizman/telegram/` — read-only Telegram adapter (long polling, 16 команд) над тем же Core; токен и allowlist chat-id только через env.
- `bizman.core.rebuild_agent_index` / CLI `bizman index-rebuild` — path-free пересборка derived Agent Index без передачи пути к БД.
- Обычные agent/read/MCP запросы должны идти через Core, а не читать repository JSON/JSONL, detector SQLite или Agent Index напрямую.

## HTTP / протокол

- `knowledge/http/application-events/index.json` — 555 значимых first-party событий.
- `knowledge/http/post-observations.jsonl` — 17 реально наблюдавшихся POST.
- `knowledge/actions/catalog.json` — 11 нормализованных write-action типов.
- `knowledge/http/endpoints/index.json` — 68 network endpoint signatures.
- `knowledge/http/routes/index.json` — 761 маршрута из HTML/JavaScript.
- `knowledge/http/forms/index.json` — 87 уникальных HTML-форм.
- `knowledge/forms/parameters.json` — 74 параметра форм с sample values.
- `knowledge/http/json-responses/index.json` — 36 JSON-response наблюдений.
- `knowledge/http/session-assets/index.json` — 7 статических ресурсов и query-key семейств из захвата 2026-10-03, отсутствовавших в каноническом census.
- `knowledge/http/session-endpoints/index.json` — 51 endpoint из read-only обхода 2026-10-03.
- `knowledge/http/surface-probe/index.json` — 94 результата проверки поверхностей без наблюдавшихся сетевых событий.
- `knowledge/http/operation-index.json` — компактный индекс важных операций.

## HTML и JavaScript

- `knowledge/pages/index.json` — 180 нормализованных игровых HTML-страниц.
- `knowledge/javascript/script-index.json` — 29 уникальных JS-ресурсов с hashes.
- `knowledge/javascript/relevant-snippets.jsonl` — 14 релевантных protocol snippets.
- `knowledge/http/assets/index.json` — 914 записей census статических ресурсов без бинарников.

## Доменная модель

- `knowledge/domain/products/index.json` — 303 товара (каталог с устойчивыми ID и наблюдёнными numeric ID).
- `knowledge/domain/product-categories/index.json` — 18 категорий товаров, восстановленных из вики-справочника; неоднозначные имена сохранены со всеми кандидатами.
- `knowledge/domain/product-attributes/index.json` — 301 запись с документированными атрибутами и рецептами из help-страниц снапшота: вес, объём, хранение, розничная группа, производство, материалы/качество/энергия.
- `knowledge/domain/buildings.json` — 71 запись: 48 зданий с ценниками ровно в том виде, как они записаны в вики, плюс numeric_id и требуемые материалы для всех 71 строительных страниц снапшота.
- `knowledge/domain/enterprise-types/index.json` — 49 видов предприятий: отрасль, требуемая характеристика и уровень компании, подразделения, схемы производства.
- `knowledge/domain/currency-rates.json` — 7 официальных курсов валют (базовая строка — российский рубль).
- `knowledge/domain/entities.json` — 19 наблюдавшихся company/city/unit сущностей.
- `knowledge/entities/observed-ids.json` — дополнительные извлечённые ID, если нужны низкоуровневые связи.

## Wiki

- `docs/wiki/INDEX.md` — навигация по Wiki-корпусу.
- `knowledge/wiki/topics/index.json` — 87 полных нормализованных Wiki-тем.
- `knowledge/wiki/recovered-topics/index.json` — 89 тем, восстановленных 2026-10-03: явная ревизия канонического корпуса с причиной расхождения и ссылкой на наблюдение.
- `knowledge/wiki/navigation.json` — taxonomy: 89 navigation topics, из них 87 захвачены.
- `knowledge/game-help/index.json` — 34 записи первого help-корпуса: 27 тем, 6 справочных страниц и анонс БМ-8.
- `knowledge/legal/index.json` — 4 записи: правила игры и три игровых закона с редакциями.

## Проверка

Текущий dependency/CI authority — `pyproject.toml` + `uv.lock`. Локально основной детерминированный gate:

```bash
uv lock --check
uv sync --locked
uv run ruff check
uv run lint-imports
uv run python -m compileall -q src tools tests
uv run --locked --with coverage==7.16.1 coverage run -m unittest discover -s tests -v
uv run --locked --with coverage==7.16.1 coverage xml
uv run python tools/validate_repo.py
uv run python tools/evaluations/mcp_p3.py
```

GitHub workflow `.github/workflows/collector-e2e.yml` запускается для релевантных pull request и вручную через `workflow_dispatch`. Он включает Python 3.14 full validation, Python 3.11 compatibility, gating P3 MCP completion evaluation, real Chrome E2E и benchmark jobs. Актуальная политика описана в `docs/CI.md`, этапы развития — в `docs/ROADMAP.md`.

## Правило для агентов

Начинайте с `knowledge/catalog.json`, затем открывайте manifest конкретного корпуса и только нужные `part-*` файлы. Не читайте весь corpus без необходимости и не превращайте `inferred`/`hypothesis` в `observed` без evidence.
