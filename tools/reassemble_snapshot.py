#!/usr/bin/env python3
from pathlib import Path
import base64
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "snapshot" / "parts"
OUT = ROOT / "bizman-curated-2026-09-06.zip"
EXPECTED = "ea76d8d3147d56046c01e3c705837114bb86eaaf30b0bd2de7f22d156998243c"

encoded = "".join(p.read_text(encoding="ascii").strip() for p in sorted(PARTS.glob("part-*.b64")))
data = base64.b64decode(encoded, validate=True)
sha = hashlib.sha256(data).hexdigest()
if sha != EXPECTED:
    raise SystemExit(f"SHA-256 mismatch: {sha} != {EXPECTED}")
OUT.write_bytes(data)
print(f"Wrote {OUT} ({len(data)} bytes), SHA-256 {sha}")
