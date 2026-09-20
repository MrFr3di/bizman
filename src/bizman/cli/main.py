from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from bizman.core import (
    AssetError,
    BizManError,
    CoreContext,
    RepositoryAssets,
    SystemUtcClock,
)
from bizman.cli import collect as collect_command
from bizman.cli import detect as detect_command
from bizman.cli import validate as validate_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bizman",
        description="Deterministic BizMan evidence and state tooling.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_command.configure_parser(subparsers.add_parser("collect"))
    detect_command.configure_parser(subparsers.add_parser("detect"))
    validate_command.configure_parser(subparsers.add_parser("validate"))
    return parser


def _context(args: argparse.Namespace) -> CoreContext:
    try:
        assets = RepositoryAssets(Path(args.repo_root))
    except (OSError, ValueError) as exc:
        raise AssetError("repository assets are unavailable or invalid") from exc
    data_dir = Path(getattr(args, "data_dir", Path.home() / "BizManData"))
    return CoreContext(
        assets=assets,
        data_dir=data_dir,
        clock=SystemUtcClock(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        context = _context(args)
        return int(args.handler(context, args))
    except BizManError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


__all__ = ["main"]
