# Индекс проекта

## Архитектура
- `docs/architecture/storage.md` — уровни хранения и правила Git/raw.
- `docs/architecture/provenance.md` — provenance, evidence и уровни уверенности.

## Протокол BizMania
- `docs/protocol/observed-endpoints.md` — подтверждённые POST и важные служебные маршруты.
- `knowledge/http/post-observations.jsonl` — все наблюдавшиеся first-party POST.
- `knowledge/http/endpoint-census.json` — агрегированный каталог application endpoints.
- `knowledge/http/forms.json` — формы из захваченного HTML.

## Исходные захваты
- `docs/research/captures.md`
- `knowledge/sources/captures.json`

## Wiki
- `docs/wiki/INDEX.md`
- `knowledge/wiki/topic-index.json` — поисковый индекс тем.
- Полные тексты Wiki находятся в полном curated snapshot: `snapshot/README.md`.

## Низкоуровневый корпус
Полный derived corpus упакован в snapshot и восстанавливается командой:

```bash
python tools/reassemble_snapshot.py
```

Внутри snapshot находятся все 555 first-party non-static события, 17 POST, endpoint/asset census, 87 форм, 36 JSON-ответов, 180 HTML-страниц, JS-индекс/snippets, route patterns и полные тексты 87 Wiki-тем.

## Работа с Codex
Читайте корневой `AGENTS.md`. Сначала ищите в `knowledge/catalog.json`, затем в соответствующем поисковом индексе. Не превращайте inference в observed fact без evidence.
