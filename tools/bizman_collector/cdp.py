from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
import json
from typing import Any, Protocol


class CdpError(RuntimeError):
    """Base class for collector-side CDP failures."""


class CdpProtocolError(CdpError):
    def __init__(self, code: int | None, message: str, data: Any = None):
        super().__init__(f"CDP protocol error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class CdpConnectionClosed(CdpError):
    """Raised when a command cannot complete because the transport closed."""


class CdpCommandRejected(CdpError):
    """Raised before sending a command that violates the passive allowlist."""


class CdpEventQueueOverflow(CdpError):
    """Raised rather than silently dropping protocol evidence."""


@dataclass(frozen=True, slots=True)
class CdpEvent:
    method: str
    params: dict[str, Any]
    session_id: str | None


class CdpTransport(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...


class CdpConnection:
    """One browser-level flattened CDP connection.

    Exactly one coroutine must run ``receive_loop``. Commands may be issued
    concurrently; responses are correlated by globally unique integer IDs.
    """

    def __init__(
        self,
        transport: CdpTransport,
        *,
        allowed_methods: frozenset[str] | None = None,
        event_queue_size: int = 4096,
    ) -> None:
        if event_queue_size <= 0:
            raise ValueError("event_queue_size must be positive")
        self._transport = transport
        self._allowed_methods = allowed_methods
        self._events: asyncio.Queue[CdpEvent] = asyncio.Queue(
            maxsize=event_queue_size
        )
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._send_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def command(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._allowed_methods is not None and method not in self._allowed_methods:
            raise CdpCommandRejected(f"CDP command is not passive-allowlisted: {method}")
        if self._closed:
            raise CdpConnectionClosed("CDP connection is closed")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async with self._send_lock:
            if self._closed:
                raise CdpConnectionClosed("CDP connection is closed")
            command_id = self._next_id
            self._next_id += 1
            self._pending[command_id] = future
            payload: dict[str, Any] = {
                "id": command_id,
                "method": method,
            }
            if params:
                payload["params"] = dict(params)
            if session_id is not None:
                payload["sessionId"] = session_id
            try:
                await self._transport.send(
                    json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                )
            except BaseException:
                self._pending.pop(command_id, None)
                if not future.done():
                    future.cancel()
                raise

        try:
            return await future
        except asyncio.CancelledError:
            self._pending.pop(command_id, None)
            raise

    async def next_event(self) -> CdpEvent:
        return await self._events.get()

    async def receive_loop(self) -> None:
        failure: BaseException | None = None
        try:
            async for raw_message in self._transport:
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    raise CdpError(f"invalid JSON received from CDP: {exc}") from exc
                if not isinstance(message, dict):
                    raise CdpError("CDP message must be a JSON object")

                command_id = message.get("id")
                if isinstance(command_id, int):
                    future = self._pending.pop(command_id, None)
                    if future is None or future.done():
                        continue
                    error = message.get("error")
                    if isinstance(error, dict):
                        code = error.get("code")
                        future.set_exception(
                            CdpProtocolError(
                                code if isinstance(code, int) else None,
                                str(error.get("message", "unknown error")),
                                error.get("data"),
                            )
                        )
                    else:
                        result = message.get("result", {})
                        future.set_result(result if isinstance(result, dict) else {})
                    continue

                method = message.get("method")
                if not isinstance(method, str):
                    continue
                params = message.get("params", {})
                if not isinstance(params, dict):
                    params = {}
                session_id = message.get("sessionId")
                event = CdpEvent(
                    method=method,
                    params=params,
                    session_id=session_id if isinstance(session_id, str) else None,
                )
                try:
                    self._events.put_nowait(event)
                except asyncio.QueueFull as exc:
                    raise CdpEventQueueOverflow(
                        "CDP event queue overflow; refusing to drop evidence"
                    ) from exc
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            failure = exc
            raise
        finally:
            self._closed = True
            close_exc = (
                failure
                if isinstance(failure, CdpConnectionClosed)
                else CdpConnectionClosed("CDP transport closed")
            )
            pending = tuple(self._pending.values())
            self._pending.clear()
            for future in pending:
                if not future.done():
                    future.set_exception(close_exc)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._transport.close()
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(CdpConnectionClosed("CDP connection closed"))


class _WebSocketTransport:
    def __init__(self, websocket: Any):
        self._websocket = websocket

    async def send(self, message: str) -> None:
        await self._websocket.send(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        async for message in self._websocket:
            if isinstance(message, bytes):
                yield message.decode("utf-8")
            else:
                yield str(message)

    async def close(self) -> None:
        await self._websocket.close()


class _CdpTransportContext:
    def __init__(
        self,
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        max_size: int,
        max_queue: int,
    ) -> None:
        self._uri = uri
        self._kwargs = {
            "open_timeout": open_timeout,
            "close_timeout": close_timeout,
            "max_size": max_size,
            "max_queue": max_queue,
            "compression": None,
            "proxy": None,
        }
        self._context: Any = None

    async def __aenter__(self) -> CdpTransport:
        from websockets.asyncio.client import connect

        self._context = connect(self._uri, **self._kwargs)
        websocket = await self._context.__aenter__()
        return _WebSocketTransport(websocket)

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._context is not None:
            await self._context.__aexit__(exc_type, exc, tb)


def open_cdp_transport(
    uri: str,
    *,
    open_timeout: float = 10.0,
    close_timeout: float = 5.0,
    max_size: int = 16 * 1024 * 1024,
    max_queue: int = 64,
) -> _CdpTransportContext:
    """Return an async context manager for the modern websockets asyncio API."""

    return _CdpTransportContext(
        uri,
        open_timeout=open_timeout,
        close_timeout=close_timeout,
        max_size=max_size,
        max_queue=max_queue,
    )
