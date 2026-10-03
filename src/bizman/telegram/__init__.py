"""Read-only BizMan Telegram adapter over the stable Core boundary."""

from bizman.telegram.bot import COMMAND_NAMES
from bizman.telegram.commands import execute
from bizman.telegram.formatting import MAX_MESSAGE_CHARS, trim

__all__ = ["COMMAND_NAMES", "MAX_MESSAGE_CHARS", "execute", "trim"]
