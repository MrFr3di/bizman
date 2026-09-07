#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

MANIFEST_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="linux64")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MANIFEST_URL, timeout=30) as response:
        manifest = json.load(response)
    stable = manifest["channels"]["Stable"]
    download = next(
        item for item in stable["downloads"]["chrome"] if item["platform"] == args.platform
    )
    archive = args.output / "chrome.zip"
    urllib.request.urlretrieve(download["url"], archive)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(args.output)
    archive.unlink()

    candidates = [path for path in args.output.rglob("chrome") if path.is_file()]
    if len(candidates) != 1:
        raise RuntimeError(f"unable to resolve Chrome binary: {candidates}")
    binary = candidates[0]
    binary.chmod(binary.stat().st_mode | 0o111)
    print(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
