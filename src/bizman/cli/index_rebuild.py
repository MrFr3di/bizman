from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from bizman.core import (
    AgentIndexRebuildRequest,
    CoreContext,
    rebuild_agent_index,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Rebuild the derived Agent Index from curated knowledge, finalized "
        "sanitized evidence and detector change summaries."
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="BizMan repository root containing curated assets.",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="External BizManData root containing sessions/events and derived indexes.",
    )
    parser.set_defaults(handler=run)


def run(context: CoreContext, args: argparse.Namespace) -> int:
    result = rebuild_agent_index(context, AgentIndexRebuildRequest())
    print(
        json.dumps(
            {
                "generation": result.generation,
                "schema_version": result.schema_version,
                "projection_version": result.projection_version,
                "item_count": result.item_count,
                "session_count": result.session_count,
                "change_count": result.change_count,
                "completed_at": result.completed_at,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def legacy_main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the derived Agent Index from curated knowledge, finalized "
            "sanitized evidence and detector change summaries."
        )
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    args = parser.parse_args(argv)
    forwarded = [
        "index-rebuild",
        "--repo-root",
        str(args.repo_root.expanduser().resolve()),
        "--data-dir",
        str(args.data_dir.expanduser().resolve()),
    ]
    from bizman.cli.main import main

    return main(forwarded)


__all__ = ["configure_parser", "legacy_main", "run"]
