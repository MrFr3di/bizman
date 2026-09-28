from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from bizman.core import CoreContext, RepositoryAssets, SystemUtcClock
from bizman.mcp.server import build_server


_LOG = logging.getLogger("bizman.mcp")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bizman-mcp",
        description="Run the local read-only BizMan MCP server over stdio.",
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
    try:
        context = CoreContext(
            assets=RepositoryAssets(args.repo_root),
            data_dir=args.data_dir,
            clock=SystemUtcClock(),
        )
    except (OSError, TypeError, ValueError) as exc:
        _LOG.error("startup configuration failed: %s", exc)
        return 2

    build_server(context).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
