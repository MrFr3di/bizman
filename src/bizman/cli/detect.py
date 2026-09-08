from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from bizman.core import CoreContext, DetectionRequest, detect_changes


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Replay sanitized BizMan evidence through the deterministic change detector."
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
        help="BizManData root containing sessions/events/artifacts and local detector state.",
    )
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="UUID",
        help="Process only the selected session UUIDv7; repeat for multiple sessions.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate/diff/classify without creating or modifying state or bundles.",
    )
    parser.set_defaults(handler=run)


def run(context: CoreContext, args: argparse.Namespace) -> int:
    summary = detect_changes(
        context,
        DetectionRequest(
            selected_sessions=tuple(args.session),
            dry_run=bool(args.dry_run),
        ),
    )
    print(
        json.dumps(
            summary.to_dict(),
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
        description="Replay sanitized BizMan evidence through the deterministic change detector."
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--session", action="append", default=[], metavar="UUID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--redaction-policy", type=Path, default=None)
    args = parser.parse_args(argv)

    selected_root = args.repo_root.expanduser().resolve()
    expected_policy = selected_root / "config" / "redaction-policy.json"
    if (
        args.redaction_policy is not None
        and args.redaction_policy.expanduser().resolve() != expected_policy.resolve()
    ):
        parser.error(
            "--redaction-policy must be <repo-root>/config/redaction-policy.json"
        )

    forwarded = [
        "detect",
        "--repo-root",
        str(selected_root),
        "--data-dir",
        str(args.data_dir),
    ]
    if args.dry_run:
        forwarded.append("--dry-run")
    for session_id in args.session:
        forwarded.extend(("--session", session_id))

    from bizman.cli.main import main

    return main(forwarded)


__all__ = ["configure_parser", "legacy_main", "run"]
