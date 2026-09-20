from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Sequence

from bizman.core import CollectionRequest, CoreContext, collect


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Passively collect sanitized first-party BizMania CDP events."
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="BizMan repository root containing curated assets.",
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
        help="Operational data root outside the repository.",
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        help="First-party host; may be repeated (default: bizmania.ru).",
    )
    parser.add_argument(
        "--event-queue-size",
        type=int,
        default=8192,
        help="Bounded in-memory CDP event queue (default: %(default)s).",
    )
    parser.set_defaults(handler=run)


def run(context: CoreContext, args: argparse.Namespace) -> int:
    request = CollectionRequest(
        endpoint=args.endpoint,
        hosts=tuple(args.hosts or ("bizmania.ru",)),
        event_queue_size=args.event_queue_size,
    )
    try:
        result = asyncio.run(collect(context, request))
    except KeyboardInterrupt:
        print("Collection interrupted.", file=sys.stderr)
        return 130
    print(result.session_id)
    return 0


def legacy_main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path,
) -> int:
    parser = argparse.ArgumentParser(
        description="Passively collect sanitized first-party BizMania CDP events."
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "BizManData")
    parser.add_argument("--host", action="append", dest="hosts")
    parser.add_argument(
        "--redaction-policy",
        type=Path,
        default=repo_root / "config" / "redaction-policy.json",
    )
    parser.add_argument("--event-queue-size", type=int, default=8192)
    args = parser.parse_args(argv)

    expected_policy = (repo_root / "config" / "redaction-policy.json").resolve()
    if args.redaction_policy.expanduser().resolve() != expected_policy:
        parser.error(
            "--redaction-policy must be the repository policy; use --repo-root "
            "to select another asset root"
        )

    forwarded = [
        "collect",
        "--repo-root",
        str(repo_root),
        "--endpoint",
        args.endpoint,
        "--data-dir",
        str(args.data_dir),
        "--event-queue-size",
        str(args.event_queue_size),
    ]
    for host in args.hosts or ():
        forwarded.extend(("--host", host))

    from bizman.cli.main import main

    return main(forwarded)


__all__ = ["configure_parser", "legacy_main", "run"]
