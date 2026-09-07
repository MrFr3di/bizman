#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bizman_collector.runtime import run_collection
from tools.bizman_foundation.redaction import load_redaction_policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Passively collect sanitized first-party BizMania CDP events."
    )
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:9222",
        help="Local Chrome DevTools HTTP origin (default: %(default)s)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "BizManData",
        help="Operational data root outside the repository",
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        help="First-party host; may be repeated (default: bizmania.ru)",
    )
    parser.add_argument(
        "--redaction-policy",
        type=Path,
        default=ROOT / "config" / "redaction-policy.json",
        help="Capture-time redaction policy",
    )
    parser.add_argument(
        "--event-queue-size",
        type=int,
        default=8192,
        help="Bounded in-memory CDP event queue (default: %(default)s)",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> str:
    policy = load_redaction_policy(args.redaction_policy)
    hosts = tuple(args.hosts or ("bizmania.ru",))
    return await run_collection(
        endpoint=args.endpoint,
        data_dir=args.data_dir,
        hosts=hosts,
        redaction_policy=policy,
        event_queue_size=args.event_queue_size,
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.event_queue_size <= 0:
        raise SystemExit("--event-queue-size must be positive")
    try:
        session_id = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("Collection interrupted.", file=sys.stderr)
        return 130
    print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
