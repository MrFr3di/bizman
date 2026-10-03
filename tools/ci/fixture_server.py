#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from websockets.asyncio.server import serve

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8765
WS_PORT = 8766

_COMPANY_ROSTER = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Компания Paradise · Предприятия</title></head>
<body>
<h1>Компания Paradise</h1>
<nav>Компания</nav>
<section>Предприятия</section>
<table>
  <tr><th>Город</th><th>Предприятие</th><th>Уровень</th><th>Эффект.</th></tr>
  <tr><td>Анкара</td><td>Детский магазин #33670</td><td>1</td><td>100%</td></tr>
  <tr><td>Анкара</td><td>Аптека #33676</td><td>1</td><td>100%</td></tr>
</table>
<input name="clientSecret" value="TOP_SECRET_ROSTER_INPUT">
<script>const token = "TOP_SECRET_ROSTER_SCRIPT";</script>
<div hidden><div>TOP_SECRET_ROSTER_HIDDEN_NESTED</div><span>TOP_SECRET_ROSTER_HIDDEN_TAIL</span></div>
<div style="color:red; display:none">TOP_SECRET_ROSTER_HIDDEN_STYLE</div>
<section aria-hidden="true">TOP_SECRET_ROSTER_HIDDEN_ARIA</section>
</body>
</html>""".encode("utf-8")

_GOODS_UNIT_ID = "33670"
_GOODS_PRODUCTS = (880001, 880002)
_GOODS_SECRETS = (
    "TOP_SECRET_GOODS_LABEL",
    "TOP_SECRET_GOODS_ATTR",
    "TOP_SECRET_GOODS_INPUT",
)


def _goods_row(
    *,
    index: int,
    product: int,
    revenue: int,
    profit: int,
    stock_qty: int,
    stock_quality: str,
    our_price: int,
    city_quality: str,
    city_price: int,
    sales_volume: int,
    supply_qty: int,
    supply_cost: int,
) -> str:
    href = (
        f"/units/shop/?id={_GOODS_UNIT_ID}&tab=goods&product={product}"
    )
    cells = (
        f'<td><a href="{href}"><img src="/img/p.png" '
        f'alt="{_GOODS_SECRETS[1]}"></a>'
        f'<input type="hidden" name="product[{index}]" value="{product}">'
        f'<input type="hidden" name="clientSecret" value="{_GOODS_SECRETS[2]}"></td>',
        f'<td><a href="{href}">{_GOODS_SECRETS[0]} {product}</a></td>',
        '<td><div class="progress" title="50%"></div></td>',
        f"<td>{revenue} p. {profit} p. "
        '<span title="Рентабельность: 17%"></span></td>',
        "<td>10%</td>",
        f"<td>{stock_qty}</td>",
        f"<td>{stock_quality}</td>",
        "<td>800 p. (700 p.)</td>",
        '<td><a href="#graph">graph</a></td>',
        f'<td><input name="price[{index}]" value="{our_price}"></td>',
        f"<td>{city_quality}</td>",
        f"<td>{city_price}</td>",
        f"<td>{sales_volume}</td>",
        '<td><a href="#dialog">dialog</a></td>',
        f'<td><input name="purchaseQuantity[{index}]" value="{supply_qty}">'
        f'<input type="hidden" name="vendorPrice[{index}]" value="{supply_cost}"></td>',
        '<td><a href="#buy">buy</a></td>',
    )
    return f'<tr id="pr{product}">{"".join(cells)}</tr>'


_GOODS_HEADER = (
    '<tr class="tblh">'
    + "".join('<td class="tblh">Header</td>' for _ in range(16))
    + "</tr>"
)
_GOODS_PAGE = (
    "<!doctype html><html><body><table id=\"goods\">"
    + _GOODS_HEADER
    + _GOODS_HEADER
    + _goods_row(
        index=0,
        product=_GOODS_PRODUCTS[0],
        revenue=1500,
        profit=250,
        stock_qty=20,
        stock_quality="4.0 (3.0)",
        our_price=300,
        city_quality="4.5",
        city_price=310,
        sales_volume=5,
        supply_qty=3,
        supply_cost=120,
    )
    + _goods_row(
        index=1,
        product=_GOODS_PRODUCTS[1],
        revenue=2500,
        profit=400,
        stock_qty=30,
        stock_quality="3.5 (2.5)",
        our_price=350,
        city_quality="3.75",
        city_price=360,
        sales_volume=6,
        supply_qty=4,
        supply_cost=130,
    )
    + "</table></body></html>"
).encode("utf-8")


_PAGE = f"""<!doctype html>
<html>
<head><meta charset=\"utf-8\"><title>BizMan collector fixture</title></head>
<body>
<form id=\"action-form\" action=\"/api/action-post?token=TOP_SECRET_ACTION_QUERY\" method=\"post\">
  <input name=\"product\" value=\"42\">
  <input name=\"clientSecret\" value=\"TOP_SECRET_INPUT_VALUE\">
  <button id=\"action-submit\" type=\"submit\">Submit</button>
</form>
<script>
const actionForm = document.getElementById('action-form');
actionForm.addEventListener('submit', (event) => {{
  event.preventDefault();
  const body = new URLSearchParams(new FormData(actionForm));
  fetch(actionForm.action, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body,
  }}).then(() => {{
    document.body.dataset.actionDone = '1';
  }}).catch(console.error);
}});

async function exerciseNetwork() {{
  const rosterResponse = await fetch('/company/?id=13393&tab=units&p=1');
  await rosterResponse.text();
  const goodsResponse = await fetch('/units/shop/?id=33670&tab=goods');
  await goodsResponse.text();
  await fetch('/api/get?safe=1&accessToken=TOP_SECRET_QUERY');
  await fetch('/api/post', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{safe: 'kept', clientSecret: 'TOP_SECRET_BODY'}})
  }});
  await fetch('/redirect');
  const frame = document.createElement('iframe');
  frame.src = '/frame';
  document.body.appendChild(frame);
  const ws = new WebSocket('ws://{HTTP_HOST}:{WS_PORT}/socket?token=TOP_SECRET_WS_QUERY');
  ws.onopen = () => {{
    ws.send('TOP_SECRET_WS_PAYLOAD');
    setTimeout(() => ws.close(), 250);
  }};

  actionForm.requestSubmit(document.getElementById('action-submit'));
  document.body.dataset.done = '1';
}}
setTimeout(() => exerciseNetwork().catch(console.error), 4000);
</script>
</body>
</html>""".encode("utf-8")


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, _PAGE, "text/html; charset=utf-8")
        elif path == "/company/":
            self._send(200, _COMPANY_ROSTER, "text/html; charset=utf-8")
        elif path == "/units/shop/":
            self._send(200, _GOODS_PAGE, "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif path == "/api/get":
            self._send(200, b'{"ok":true}', "application/json")
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/final":
            self._send(200, b"final", "text/plain")
        elif path == "/frame":
            self._send(200, b"<!doctype html><p>frame</p>", "text/html")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        if path == "/api/post":
            self._send(200, b'{"saved":true}', "application/json")
        elif path == "/api/action-post":
            self._send(200, b'{"action":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")


async def websocket_handler(connection) -> None:  # type: ignore[no-untyped-def]
    async for _message in connection:
        await connection.send("ACK")


async def main() -> None:
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with serve(websocket_handler, HTTP_HOST, WS_PORT):
            print("READY", flush=True)
            await asyncio.Event().wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
