from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import fields
import json
from pathlib import Path

from bizman.core import (
    CoreContext,
    CurrentCompanyListRequest,
    CurrentProductListRequest,
    CurrentStateRebuildRequest,
    CurrentStatusRequest,
    CurrentUnitListRequest,
    OperationError,
    current_status,
    list_current_companies,
    list_current_products,
    list_current_units,
    rebuild_current_state,
)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="BizMan repository root containing curated assets.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "BizManData",
        help="BizManData root containing the derived Current State database.",
    )


def _add_page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum records in one bounded page (default: %(default)s).",
    )
    parser.add_argument(
        "--cursor",
        default=None,
        help="Opaque Core cursor from a previous page of the same query.",
    )


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Rebuild and read the deterministic BizMan Current State projection."
    subparsers = parser.add_subparsers(dest="current_command", required=True)

    rebuild = subparsers.add_parser(
        "rebuild",
        description="Rebuild Current State from immutable finalized evidence.",
    )
    _add_common_arguments(rebuild)

    status = subparsers.add_parser(
        "status",
        description="Show Current State projection identity and replay status.",
    )
    _add_common_arguments(status)

    companies = subparsers.add_parser(
        "companies",
        description="List bounded Current State companies.",
    )
    _add_common_arguments(companies)
    _add_page_arguments(companies)

    units = subparsers.add_parser(
        "units",
        description="List bounded Current State units, optionally scoped to a company.",
    )
    _add_common_arguments(units)
    _add_page_arguments(units)
    units.add_argument(
        "--company",
        dest="company_id",
        default=None,
        help="Restrict units to one canonical company identifier.",
    )

    products = subparsers.add_parser(
        "products",
        description="List bounded Current State unit products, optionally scoped to a unit.",
    )
    _add_common_arguments(products)
    _add_page_arguments(products)
    products.add_argument(
        "--unit",
        dest="unit_id",
        default=None,
        help="Restrict products to one canonical unit identifier.",
    )

    for subparser in (rebuild, status, companies, units, products):
        subparser.set_defaults(handler=run)


def _fields(value: object) -> dict[str, object]:
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _print_json(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _request(factory: Callable[..., object], **kwargs: object) -> object:
    try:
        return factory(**kwargs)
    except (TypeError, ValueError) as exc:
        raise OperationError(f"invalid Current State request: {exc}") from exc


def run(context: CoreContext, args: argparse.Namespace) -> int:
    command = args.current_command
    if command == "rebuild":
        result = rebuild_current_state(context, CurrentStateRebuildRequest())
        _print_json(_fields(result))
        return 0
    if command == "status":
        result = current_status(context, CurrentStatusRequest())
        _print_json(_fields(result))
        return 0
    if command == "companies":
        request = _request(
            CurrentCompanyListRequest,
            limit=args.limit,
            cursor=args.cursor,
        )
        page = list_current_companies(context, request)
    elif command == "units":
        request = _request(
            CurrentUnitListRequest,
            limit=args.limit,
            cursor=args.cursor,
            company_id=args.company_id,
        )
        page = list_current_units(context, request)
    elif command == "products":
        request = _request(
            CurrentProductListRequest,
            limit=args.limit,
            cursor=args.cursor,
            unit_id=args.unit_id,
        )
        page = list_current_products(context, request)
    else:  # pragma: no cover - argparse guarantees a known subcommand
        raise OperationError(f"unknown Current State command: {command}")
    _print_json(
        {
            "items": [_fields(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }
    )
    return 0


__all__ = ["configure_parser", "run"]
