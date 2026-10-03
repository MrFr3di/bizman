"""Read-only BizMan Telegram adapter over the stable Core boundary."""

from bizman.telegram.commands import COMMAND_NAMES, execute
from bizman.telegram.formatting import MAX_MESSAGE_CHARS, trim

__all__ = ["COMMAND_NAMES", "MAX_MESSAGE_CHARS", "execute", "trim"]
