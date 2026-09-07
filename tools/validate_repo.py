#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from bizman_foundation.validation import validate_repository

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    result = validate_repository(ROOT)
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


if __name__ == "__main__":
    raise SystemExit(main())
