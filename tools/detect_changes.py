#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bizman_detector.runner import DetectorRunner
from tools.bizman_foundation.redaction import load_redaction_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay sanitized BizMan evidence through the deterministic change detector."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="BizManData directory containing sessions/events/artifacts and local detector state.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="BizMan repository root used for curated knowledge and schemas.",
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
        help="Validate/diff/classify without creating or modifying detector state or bundles.",
    )
    parser.add_argument(
        "--redaction-policy",
        type=Path,
        default=None,
        help="Redaction policy JSON; defaults to <repo-root>/config/redaction-policy.json.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve(strict=False)
    policy_path = (
        args.redaction_policy.expanduser().resolve()
        if args.redaction_policy is not None
        else repo_root / "config" / "redaction-policy.json"
    )
    redaction = load_redaction_policy(policy_path)

    with DetectorRunner.from_paths(
        repo_root=repo_root,
        data_dir=data_dir,
        redaction=redaction,
        selected=tuple(args.session),
        dry_run=bool(args.dry_run),
    ) as runner:
        summary = runner.run()

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


if __name__ == "__main__":
    raise SystemExit(main())
