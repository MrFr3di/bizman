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
from bizman.telegram.commands import COMMAND_NAMES, is_authorized, parse_allowed_chat_ids

_LOG = logging.getLogger("bizman.telegram")


def build_router(
    context: CoreContext,
    allowed_chat_ids: frozenset[int],
) -> Router:
    router = Router(name="bizman-telegram")

    @router.message(Command(*COMMAND_NAMES))
    async def on_command(message: Message) -> None:
        if not is_authorized(message.chat.id, allowed_chat_ids):
            return
        reply = await asyncio.to_thread(commands.execute, context, message.text or "")
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
