"""aiogram wiring for the read-only BizMan Telegram adapter.

The transport layer stays deliberately thin: it authorizes chat ids, forwards
one message line to ``bizman.telegram.commands.execute`` and sends the reply.
No game writes, no arbitrary files, no callbacks: replies are plain text and
pagination beyond the first page is intentionally out of the MVP scope.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from bizman.core import CoreContext
from bizman.telegram import commands

_LOG = logging.getLogger("bizman.telegram")

COMMAND_NAMES: frozenset[str] = frozenset(
    (
        "help",
        "status",
        "sessions",
        "session",
        "compare",
        "anomalies",
        "changes",
        "change",
        "k",
        "kb",
        "trace",
    )
)


def parse_allowed_chat_ids(raw: str | None) -> frozenset[int]:
    """Parse the strict chat allowlist; missing/invalid configuration fails closed."""
    if raw is None:
        raise ValueError("BIZMAN_TELEGRAM_CHAT_IDS is not set")
    identifiers: set[int] = set()
    for part in raw.split(","):
        text = part.strip()
        if not text:
            raise ValueError("BIZMAN_TELEGRAM_CHAT_IDS contains an empty entry")
        identifiers.add(int(text))
    if not identifiers:
        raise ValueError("BIZMAN_TELEGRAM_CHAT_IDS must contain at least one chat id")
    return frozenset(identifiers)


def is_authorized(chat_id: int, allowed_chat_ids: frozenset[int]) -> bool:
    return chat_id in allowed_chat_ids


def build_router(
    context: CoreContext,
    allowed_chat_ids: frozenset[int],
) -> Router:
    router = Router(name="bizman-telegram")

    @router.message(Command(*COMMAND_NAMES))
    async def on_command(message: Message) -> None:
        if not is_authorized(message.chat.id, allowed_chat_ids):
            return
        reply = commands.execute(context, message.text or "")
        await message.answer(reply)

    return router


def build_dispatcher(
    context: CoreContext,
    allowed_chat_ids: frozenset[int],
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(context, allowed_chat_ids))
    return dispatcher


async def run_polling(
    context: CoreContext,
    token: str,
    allowed_chat_ids: frozenset[int],
) -> None:
    bot = Bot(token=token)
    dispatcher = build_dispatcher(context, allowed_chat_ids)
    await dispatcher.start_polling(bot, allowed_updates=["message"])


def run_polling_sync(
    context: CoreContext,
    token: str,
    allowed_chat_ids: frozenset[int],
) -> None:
    try:
        asyncio.run(run_polling(context, token, allowed_chat_ids))
    except KeyboardInterrupt:
        _LOG.info("stopping BizMan Telegram adapter")


__all__ = [
    "COMMAND_NAMES",
    "build_dispatcher",
    "build_router",
    "is_authorized",
    "parse_allowed_chat_ids",
    "run_polling",
    "run_polling_sync",
]
