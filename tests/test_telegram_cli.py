from __future__ import annotations

import io
import logging
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest import mock

from bizman.core import CoreContext, RepositoryAssets, SystemUtcClock
from bizman.telegram import __main__ as telegram_main
from bizman.telegram import commands

try:
    from bizman.telegram import bot as telegram_bot
except ImportError:  # pragma: no cover - only without the telegram extra
    telegram_bot = None

REPO_ROOT = Path(__file__).resolve().parents[1]


class AllowedChatIdsTests(unittest.TestCase):
    def test_missing_config_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            commands.parse_allowed_chat_ids(None)

    def test_valid_list_is_parsed_and_stripped(self) -> None:
        self.assertEqual(commands.parse_allowed_chat_ids("1, 2,  3"), frozenset({1, 2, 3}))

    def test_empty_entries_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            commands.parse_allowed_chat_ids("")
        with self.assertRaises(ValueError):
            commands.parse_allowed_chat_ids("1,,")

    def test_non_numeric_entries_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            commands.parse_allowed_chat_ids("abc")

    def test_membership(self) -> None:
        allowed = frozenset({1, 2})
        self.assertTrue(commands.is_authorized(1, allowed))
        self.assertFalse(commands.is_authorized(3, allowed))


class TelegramCliStartupTests(unittest.TestCase):
    def test_main_fails_closed_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("BIZMAN_TELEGRAM_BOT_TOKEN", "BIZMAN_TELEGRAM_CHAT_IDS")
            }
            with mock.patch.dict(os.environ, env, clear=True):
                exit_code = telegram_main.main(
                    ["--repo-root", str(REPO_ROOT), "--data-dir", tmp],
                )
        self.assertEqual(exit_code, 2)

    def test_main_fails_closed_without_chat_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("BIZMAN_TELEGRAM_BOT_TOKEN", "BIZMAN_TELEGRAM_CHAT_IDS")
            }
            env["BIZMAN_TELEGRAM_BOT_TOKEN"] = "token"
            with mock.patch.dict(os.environ, env, clear=True):
                exit_code = telegram_main.main(
                    ["--repo-root", str(REPO_ROOT), "--data-dir", tmp],
                )
        self.assertEqual(exit_code, 2)

    def test_main_fails_closed_on_invalid_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "BIZMAN_TELEGRAM_BOT_TOKEN": "token",
                "BIZMAN_TELEGRAM_CHAT_IDS": "1",
            }
            with mock.patch.dict(os.environ, env, clear=True):
                exit_code = telegram_main.main(
                    ["--repo-root", str(Path(tmp) / "missing"), "--data-dir", tmp],
                )
        self.assertEqual(exit_code, 2)

    def test_main_fails_closed_when_telegram_extra_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "BIZMAN_TELEGRAM_BOT_TOKEN": "token",
                "BIZMAN_TELEGRAM_CHAT_IDS": "1",
            }
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            logger = logging.getLogger("bizman.telegram")
            logger.addHandler(handler)
            package = sys.modules.get("bizman.telegram")
            saved_bot = getattr(package, "bot", None)
            try:
                sys.modules.pop("bizman.telegram.bot", None)
                if package is not None and saved_bot is not None:
                    delattr(package, "bot")
                with mock.patch.dict(os.environ, env, clear=True):
                    with mock.patch.dict(sys.modules, {"aiogram": None}):
                        exit_code = telegram_main.main(
                            ["--repo-root", str(REPO_ROOT), "--data-dir", tmp],
                        )
            finally:
                logger.removeHandler(handler)
                if package is not None and saved_bot is not None:
                    package.bot = saved_bot
                    sys.modules["bizman.telegram.bot"] = saved_bot
        self.assertEqual(exit_code, 2)
        self.assertIn("bizman[telegram]", stream.getvalue())


class RouterBuildTests(unittest.TestCase):
    @unittest.skipUnless(telegram_bot is not None, "aiogram extra not installed")
    def test_router_registers_command_handler(self) -> None:
        context = CoreContext(
            assets=RepositoryAssets(REPO_ROOT),
            data_dir=Path(tempfile.gettempdir()) / "bizman-telegram-router-test",
            clock=SystemUtcClock(),
        )
        router = telegram_bot.build_router(context, frozenset({1}))
        self.assertEqual(len(router.message.handlers), 1)
        handler = router.message.handlers[0]
        self.assertTrue(handler.callback.__name__.startswith("on_command"))

    def test_command_surface_matches_registry(self) -> None:
        self.assertEqual(
            frozenset(commands.COMMAND_NAMES),
            frozenset(
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
            ),
        )


if __name__ == "__main__":
    unittest.main()
