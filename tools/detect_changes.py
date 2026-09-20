#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bizman.cli.detect import legacy_main


def main(argv: Sequence[str] | None = None) -> int:
    return legacy_main(argv, repo_root=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
