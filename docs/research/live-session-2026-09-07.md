# Live-сессия 2026-09-07 (CDP)

Первая живая сессия пассивного коллектора (`tools/collect_live.py`) поверх
корпуса из 3 HAR. Оперативные данные лежат вне Git (`H:\BizManData`);
сюда promoted только санитизированные производные наблюдения.

## Сессия

| Поле | Значение |
|---|---|
| source key | `live-cdp-2026-09-07` |
| session id (UUIDv7, runtime) | `01a07d18-cf20-7207-bbf7-d5fa8a87f103` |
| период UTC | 2026-09-07T18:19:33Z — ~19:05Z |
| браузер | Chrome 150.0.7871.187, отдельный профиль, `--remote-debugging-port=9222` |
| событий извлечено | 32 128 (`http.request` 10 915, `http.response` 10 600, `dom.action` 12, `correlation.action_http` 2) |
| GET / POST-запросов | 32 082 / 12 документов-запросов (4 login, 1 create1, 1 calculator, 4 level, 2 magicpurchase) |
| компания / города | Paradise `id=13393`; Ереван `id=25`; Анкара `id=24` (подтверждено игроком) |
| юниты | аптека `33676` / дивизион `33677`; детский магазин `33670` / дивизион `33671` (оба — Анкара, со слов игрока) |

Коллектор молчит в консоли пока работает — это нормально; `session_id`
печатается после `Ctrl+C`. Редакция — `config/redaction-policy.json`,
WebSocket-пейлоады не хранились.

## POST (все `200 text/html`, тела — санитизированные)

| seq | path | query | тело (ключи=значения) |
|---:|---|---|---|
| 25730 | `/units/produce/calculator/` | — | `city=25, product=1, oldCurrency=RUB, dressingModifier=0%, techLabourModifier=0%, p=1, d=1, sort=null, oper=(пусто), $post=(пусто)` |
| 28821 | `/units/level/` | `division=33677, id=33676` | `divisionId=33677, licenceId=(пусто), p=1, $scene=first, sort=null, $post=on` |
| 28931 | `/units/level/` | `id=33676` | `division=33677, levelnumber=3, hireStaff=on, p=1, $scene=division, sort=null, $post=on` |
| 29258 | `/units/vendor/magicpurchase/` | `id=33676` | `oper=replace, selected[339]=on, selected[340]=on, selectAllCheckbox=on, p=1, sort=null, $post=on` |
| 31476 | `/units/level/` | `division=33671, id=33670` | как 28821, для `33671` |
| 31566 | `/units/level/` | `id=33670` | как 28931, для `33671` |
| 31885 | `/units/vendor/magicpurchase/` | `id=33670` | `oper=replace, selected[426]=on, selected[429]=on, selectAllCheckbox=on, ...` |

Паттерн улучшения двушаговый: `$scene=first` (открытие, без жеста) →
`$scene=division` с `levelnumber=3 + hireStaff=on` (подтверждение, с жестом).
Оба юнита подняты до 3-го уровня; в обоих выполнена `oper=replace`.

## Навигация (страница → маршрут)

Отчёт компании `id=13393`: `section=units/industries` (`category=1`);
`simple/accountlog/assets/charts/units/industries` (`category=11`);
`productSummary` (`category=11/18/22`); `companyRequests/accountlog`
(`category=22`); `regionstat/accountlogdaily` (`category=22`);
пагинация `p=1/2/3/4/9/60`, отсутствие `p` = 1-я страница.

Город `id=25`: `tab=units/auctions/retailmarket`; карта
(`/city/map/ cmd=init/get`, `/city/data`, спрайты `/fl/sprites/*`);
`retailmarket sub=retail/produce/construction/circulation/deficit/taxpayers/forsale/pricechange/averageprice/leaders/unitslog`,
`circulation category=1..22`, `retail sub=retail p=2 sort=-percent`.

Глобальная аналитика: `/analitics/retailmarket/ (+?city=25/24)`,
`/analitics/vendors/`, `/analitics/cityretaildeficit/`,
`/analitics/retailprices/ (+?retailgroup=14)`, `/analitics/tredva/`,
`/analitics/construction`, `/analitics/cities (+?tab=competitors/retail/produce)`,
`/analitics/commoditycirculation/ (+?category=1&countryId=1/14/27/100000000)`,
`/analitics/supplysummary`.

Аптека `33676` / детский `33670`: `tab=goods/supply/divisions/reports`,
`reports section=assets/accountlog`, `/units/division/create/`,
`/units/level/`, `/units/vendor/magicpurchase`.

## Справочники, выведенные из сессии

Категории (`category=` сквозные для компании и города):
`1` ископаемые, `2` материалы, `3` компоненты, `4` с/х продукция,
`5` продукты, `6` ТНП, `7` стройматериалы, `8` промтовары, `9` животные,
`11` электроника, `16` фармацевтика, `17` спорт, `18` одежда и обувь,
`19` автотовары, `20` люкс, `22` детские.
Машиночитаемо: `knowledge/domain/analytics-dimensions.json`.

Страны (`countryId=`): `1` Россия, `14` Европа, `27` Азия
(выборы игрока, подписи игрока), `100000000` весь мир (`inferred`, сентинел).

Продукты: `1` = Нефть (подтверждено игроком, калькулятор);
`339/340` — выбор поставщиков аптеки; `426/429` — детского;
`characterId=148` — стройматериалы по умолчанию (`inferred`).

## Что подтверждено из корпуса, что новое

Подтверждено (уже было в HAR-корпусе, те же id): базовые паттерны
`/company/?id&tab=*`, `/city/?id&tab=*`, табы `goods/supply/divisions/reports`,
`magicpurchase`, `level`, `division/create`, юниты `33676/33670`, города `25`,
`retailgroup=14`, `tredva`.
Новое: 29 route-паттернов (все `section=` отчёта компании, карта,
`unitlevel+division`, все `/analitics/*` как исполняемые маршруты);
3 write-action (`produce.calculator`, `level`, `vendor.magicpurchase`);
4 операции `op-012…op-015`; 12 параметров (`count` 62→74);
сущность `bm.city.24`; измерения `analytics-dimensions.json`.

## Не promoted и почему

- `POST /user/login/ ×4` — auth-трафик принципиально не contributes в публичный корпус.
- `POST /units/create1/ ×1` (`$scene=clear`, без жеста) — контекст не атрибутирован игроком; открыт вопрос.
- `post-observations.jsonl` не расширен: у формата HAR-entry паритет
  (`capture#entry` + архив ответа); live-POST полностью представлены в
  `actions/catalog.json` + `operation-index.json` + здесь.
- Ответные тела live-POST не закоммичены: артефакты сессии остаются в `BizManData`.
- Имена «Европа/Азия», `characterId=148`, «весь мир» — `observed`/`inferred`
  по подписям игрока, требуют перепроверки текстом страниц.
