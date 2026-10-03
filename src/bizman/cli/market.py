from __future__ import annotations

import argparse
import json
from pathlib import Path

from bizman.core import CoreContext, OperationError
from bizman.core.market import MarketProjectionRequest, market_projection


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="BizMan repository root containing curated assets.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "BizManData",
        help="BizManData root containing the derived market database.",
    )


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Read the deterministic latest-observation market projection."
    )
    subparsers = parser.add_subparsers(dest="market_command", required=True)

    status = subparsers.add_parser(
        "status",
        description="Show market projection identity and entry counts.",
    )
    _add_common_arguments(status)
    status.add_argument(
        "--surface",
        default=None,
        help="Restrict the projection to one known market surface.",
    )
    status.set_defaults(handler=run)


def _print_json(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def run(context: CoreContext, args: argparse.Namespace) -> int:
    command = args.market_command
    if command != "status":  # pragma: no cover - argparse guarantees status
        raise OperationError(f"unknown Market command: {command}")
    try:
        request = MarketProjectionRequest(surface=args.surface)
    except (TypeError, ValueError) as exc:
        raise OperationError(f"invalid Market request: {exc}") from exc
    result = market_projection(context, request)
    _print_json(
        {
            "available": True,
            "entry_count": result.entry_count,
            "observation_count": result.observation_count,
            "projection_fingerprint": result.projection_fingerprint,
            "surface": result.surface,
        }
    )
    return 0


__all__ = ["configure_parser", "run"]
