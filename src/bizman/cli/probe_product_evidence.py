from __future__ import annotations

import argparse
import json
from pathlib import Path

from bizman.collector.product_probe import (
    ProductProbeRoute,
    inspect_product_identity_candidates,
)
from bizman.core import CoreContext


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


def run(context: CoreContext, args: argparse.Namespace) -> int:
    del context
    try:
        route = ProductProbeRoute.parse(
            args.source_url,
            approved_hosts=("bizmania.ru",),
        )
        size = args.body_file.stat().st_size
        if size > _MAX_PROBE_BODY_BYTES:
            raise ValueError("probe body exceeds the research size limit")
        body = args.body_file.read_bytes()
        candidates = inspect_product_identity_candidates(body)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"product evidence probe failed: {type(exc).__name__}") from exc

    report = {
        "schema": "bizman.product-probe.v1",
        "unit_id": route.unit_id,
        "surface": route.surface,
        "candidate_count": len(candidates),
        "candidates": [
            {
                "tag": item.tag,
                "attribute": item.attribute,
                "numeric_query_ids": list(item.numeric_query_ids),
            }
            for item in candidates
        ],
    }
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0
