from __future__ import annotations

import argparse
import json
from pathlib import Path

from bizman.core import (
    CoreContext,
    ProductEvidenceProbeRequest,
    probe_product_evidence,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Research-only local probe for P4-C product identity evidence. "
        "The input body is read transiently and is never copied into BizManData."
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.set_defaults(handler=run)


def run(context: CoreContext, args: argparse.Namespace) -> int:
    result = probe_product_evidence(
        context,
        ProductEvidenceProbeRequest(
            source_url=args.source_url,
            body_file=args.body_file,
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
