from __future__ import annotations

import argparse
import json
from pathlib import Path

from bizman.core import CoreContext, OperationError
from bizman.core.supply_probe import (
    SupplyEvidenceProbeRequest,
    probe_supply_evidence,
)


_MAX_PROBE_BODY_BYTES = 4 * 1024 * 1024


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Research-only local probe for P4-D supply evidence. "
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
        raise OperationError("supply evidence probe input is unavailable") from exc
    if len(body) > _MAX_PROBE_BODY_BYTES:
        raise OperationError("supply evidence probe input exceeds size limit")
    return body


def run(context: CoreContext, args: argparse.Namespace) -> int:
    result = probe_supply_evidence(
        context,
        SupplyEvidenceProbeRequest(
            source_url=args.source_url,
            body=_read_bounded(args.body_file),
        ),
    )
    report = {
        "schema": "bizman.supply-probe.v1",
        "unit_id": result.unit_id,
        "surface": result.surface,
        "link_count": len(result.links),
        "input_count": len(result.inputs),
        "links": [
            {
                "path": item.path,
                "query_keys": list(item.query_keys),
                "numeric_query_fields": [
                    [name, value]
                    for name, value in item.numeric_query_fields
                ],
                "in_row": item.in_row,
                "in_form": item.in_form,
                "row_slot": item.row_slot,
                "form_slot": item.form_slot,
            }
            for item in result.links
        ],
        "inputs": [
            {
                "name": item.name,
                "index": item.index,
                "in_row": item.in_row,
                "in_form": item.in_form,
                "row_slot": item.row_slot,
                "form_slot": item.form_slot,
            }
            for item in result.inputs
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


__all__ = ["configure_parser", "run"]
