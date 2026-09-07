# Индекс проекта

## Точки входа

- `README.md` — назначение репозитория, состав корпуса и правила работы.
- `docs/ROADMAP.md` — единый архитектурный roadmap: Change Detector, Core/package migration, Agent Index, MCP, State, analytics, experiments и guarded automation.
- `knowledge/catalog.json` — главный машинный каталог с актуальными путями и counts.
- `AGENTS.md` — инструкции для Codex/агентов.
- `SECURITY.md` — правила работы с приватными HAR/session/auth данными.

## Архитектура

- `docs/ROADMAP.md` — целевая последовательность PR и acceptance gates.
- `docs/architecture/storage.md` — разделение raw/derived и правила хранения.
- `docs/architecture/provenance.md` — provenance, evidence и уровни уверенности.
- `docs/CI.md` — актуальная политика PR quality gate и Chrome for Testing E2E.
- `docs/research/captures.md` — сведения об исходных захватах.
- `knowledge/sources/captures.json` — точные SHA-256 и capture-level метаданные.

## HTTP / протокол

- `knowledge/http/application-events/index.json` — 555 значимых first-party событий.
- `knowledge/http/post-observations.jsonl` — 17 реально наблюдавшихся POST.
- `knowledge/actions/catalog.json` — 8 нормализованных write-action типов.
- `knowledge/http/endpoints/index.json` — 68 network endpoint signatures.
- `knowledge/http/routes/index.json` — 732 маршрута из HTML/JavaScript.
- `knowledge/http/forms/index.json` — 87 уникальных HTML-форм.
- `knowledge/forms/parameters.json` — 62 параметра форм с sample values.
- `knowledge/http/json-responses/index.json` — 36 JSON-response наблюдений.
- `knowledge/http/operation-index.json` — компактный индекс важных операций.

## HTML и JavaScript

- `knowledge/pages/index.json` — 180 нормализованных игровых HTML-страниц.
- `knowledge/javascript/script-index.json` — 29 уникальных JS-ресурсов с hashes.
- `knowledge/javascript/relevant-snippets.jsonl` — 14 релевантных protocol snippets.
- `knowledge/http/assets/index.json` — 914 записей census статических ресурсов без бинарников.

## Доменная модель

- `knowledge/domain/products/index.json` — 303 товара.
- `knowledge/domain/entities.json` — 18 наблюдавшихся company/city/unit сущностей.
- `knowledge/entities/observed-ids.json` — дополнительные извлечённые ID, если нужны низкоуровневые связи.

## Wiki

- `docs/wiki/INDEX.md` — навигация по Wiki-корпусу.
- `knowledge/wiki/topics/index.json` — 87 полных нормализованных Wiki-тем.
- `knowledge/wiki/navigation.json` — taxonomy: 89 navigation topics, из них 87 захвачены.

## Проверка

Локальный quality gate:

```bash
python3 -m pip install -r tools/requirements.txt
python3 -m compileall -q tools tests
python3 -m unittest discover -s tests -v
python3 tools/validate_repo.py
```

Публичный репозиторий также использует `.github/workflows/collector-e2e.yml` как `pull_request` quality gate для релевантных изменений и `workflow_dispatch` для явных повторных запусков. Routine `push` и scheduled workflows отсутствуют. Актуальные детали — в `docs/CI.md`.

## Правило для агентов

До появления Agent Index/MCP начинайте с `knowledge/catalog.json`, затем открывайте manifest конкретного корпуса и только нужные `part-*` файлы. Не читайте весь corpus без необходимости и не превращайте `inferred`/`hypothesis` в `observed` без evidence. После реализации roadmap обычные agent-запросы должны идти через стабильные refs/Core/MCP, а raw JSONL останется внутренним evidence source.
