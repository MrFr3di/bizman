"""Run the read-only BizMan Telegram adapter over long polling.

The adapter consumes the stable Core boundary only. The bot token and the
chat allowlist come exclusively from the environment:

    BIZMAN_TELEGRAM_BOT_TOKEN  — required Telegram bot token
    BIZMAN_TELEGRAM_CHAT_IDS   — required comma-separated allowlist of chat ids

Startup fails closed (exit code 2) without leaking configuration details.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import os
import sys

from bizman.core import CoreContext, RepositoryAssets, SystemUtcClock
from bizman.telegram.bot import parse_allowed_chat_ids, run_polling_sync

_LOG = logging.getLogger("bizman.telegram")

_TOKEN_ENV = "BIZMAN_TELEGRAM_BOT_TOKEN"
_CHAT_IDS_ENV = "BIZMAN_TELEGRAM_CHAT_IDS"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bizman-telegram",
        description=(
            "Run the local read-only BizMan Telegram assistant over long polling."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="BizMan repository root containing curated assets.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="External BizManData root containing the derived Agent Index.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get(_TOKEN_ENV)
    if token is None or not token.strip():
        _LOG.error("startup configuration failed: %s is not set", _TOKEN_ENV)
        return 2
    try:
        allowed_chat_ids = parse_allowed_chat_ids(os.environ.get(_CHAT_IDS_ENV))
        context = CoreContext(
            assets=RepositoryAssets(args.repo_root),
            data_dir=args.data_dir,
            clock=SystemUtcClock(),
        )
    except (OSError, TypeError, ValueError) as exc:
        _LOG.error("startup configuration failed: %s", exc)
        return 2

    _LOG.info("BizMan Telegram adapter started; authorized chats: %d", len(allowed_chat_ids))
    run_polling_sync(context, token.strip(), allowed_chat_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
