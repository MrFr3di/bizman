#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from websockets.asyncio.server import serve

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8765
WS_PORT = 8766

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
