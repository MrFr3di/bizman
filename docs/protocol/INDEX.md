# Protocol index

BizMania traffic in the supplied captures is predominantly classic HTML navigation and HTML form submission. The evidence includes normal documents, a small number of XHR requests for city/map metadata, and observed POST actions. No first-party `fetch` or WebSocket transport was observed in the supplied sessions.

- [Observed write actions](observed-actions.md)
- [Forms catalog](../../knowledge/forms/catalog.json)
- [Endpoint catalog](../../knowledge/endpoints/catalog.json)
- [Script endpoint references](../../knowledge/scripts/catalog.json)
- [All route signatures](../../knowledge/inventory/route-signatures.csv)
- [Exact POST observations](../../knowledge/observations/post-requests.jsonl)

Important distinction: `observed` = present in captured traffic; `discovered-reference` = found in HTML/JavaScript but not necessarily executed in these captures.
