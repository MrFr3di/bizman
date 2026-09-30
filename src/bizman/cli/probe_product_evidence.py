from __future__ import annotations

import argparse
import json
from pathlib import Path

from bizman.core import CoreContext, OperationError
from bizman.core.product_probe import (
    ProductEvidenceProbeRequest,
    probe_product_evidence,
)


_MAX_PROBE_BODY_BYTES = 4 * 1024 * 1024


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Research-only local probe for P4-C product identity evidence. "
        "The input body is read transiently and is never copied into BizManData."
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.set_defaults(handler=run)


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            body = stream.read(_MAX_PROBE_BODY_BYTES + 1)
    except OSError as exc:
        raise OperationError("product evidence probe input is unavailable") from exc
    if len(body) > _MAX_PROBE_BODY_BYTES:
        raise OperationError("product evidence probe input exceeds size limit")
    return body


def run(context: CoreContext, args: argparse.Namespace) -> int:
    result = probe_product_evidence(
        context,
        ProductEvidenceProbeRequest(
            source_url=args.source_url,
            body=_read_bounded(args.body_file),
        ),
    )
    report = {
        "schema": "bizman.product-probe.v1",
        "unit_id": result.unit_id,
        "surface": result.surface,
        "candidate_count": len(result.candidates),
        "candidates": [
            {
                "tag": item.tag,
                "attribute": item.attribute,
                "numeric_query_ids": list(item.numeric_query_ids),
            }
            for item in result.candidates
        ],
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0
