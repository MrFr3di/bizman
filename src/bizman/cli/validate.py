from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from bizman.core import CoreContext, validate_repository


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Validate curated BizMan repository data and repository hygiene."
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="BizMan repository root containing curated assets.",
    )
    parser.set_defaults(handler=run)


def run(context: CoreContext, args: argparse.Namespace) -> int:
    del args
    result = validate_repository(context)
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if result.errors:
        print("VALIDATION FAILED", file=sys.stderr)
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("VALIDATION OK")
    print(
        "Catalog, manifests, schemas, source identities and forbidden-file "
        "checks passed."
    )
    return 0


def legacy_main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path,
) -> int:
    if argv:
        parser = argparse.ArgumentParser(description="Validate BizMan repository data.")
        parser.parse_args(argv)

    from bizman.cli.main import main

    return main(["validate", "--repo-root", str(repo_root)])


__all__ = ["configure_parser", "legacy_main", "run"]
