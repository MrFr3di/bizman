"""Typed unit-economics artifact contract shared by collector and Current State.

The module owns only the canonical artifact representation and its strict
validator. Game HTML parsing lives in ``bizman.collector.unit_economics`` and
never crosses into this dependency leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

from bizman.foundation.fingerprint import canonical_json_bytes, canonical_sha256


SCHEMA = "bizman.unit-economics.v1"
CONTRACT_VERSION = 1
# Event-level marker that selects this artifact parser without guessing from URL text.
RESPONSE_BODY_KIND = "unit-economics.v1"
# Completeness is unknown for a single page; only positive observation is claimed.
COVERAGE_OBSERVED = "observed"
MAX_ROWS = 512

# Exactly the fields projected by P4-C v1. Supply cost is the hidden
# vendorPrice[N] value from the supply cell; the visible cost cell (td7) is not
# part of the contract.
ROW_FIELDS: tuple[str, ...] = (
    "product_numeric_id",
    "revenue",
    "profit",
    "stock_qty",
    "stock_quality",
    "our_price",
    "city_quality",
    "city_price",
    "sales_volume",
    "supply_qty",
    "supply_cost",
)
_INTEGER_FIELDS: tuple[str, ...] = (
    "product_numeric_id",
    "revenue",
    "profit",
    "stock_qty",
    "our_price",
    "city_price",
    "sales_volume",
    "supply_qty",
    "supply_cost",
)
_QUALITY_FIELDS: tuple[str, ...] = ("stock_quality", "city_quality")
_TOP_LEVEL_KEYS = frozenset(
    {"schema", "contract_version", "unit_id", "coverage", "rows"}
)
_ROW_KEYS = frozenset(ROW_FIELDS)
_MAX_SIGNED_INT64 = (1 << 63) - 1
_POSITIVE_DECIMAL_RE = re.compile(r"^[1-9][0-9]*$", re.ASCII)


class UnitEconomicsContractError(ValueError):
    """A unit-economics artifact violates the typed contract."""


def _require_int(value: object, *, name: str, positive: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnitEconomicsContractError(f"{name} must be an integer")
    if positive and value <= 0:
        raise UnitEconomicsContractError(f"{name} must be a positive integer")
    if not positive and value < 0:
        raise UnitEconomicsContractError(f"{name} must be a non-negative integer")
    if value > _MAX_SIGNED_INT64:
        raise UnitEconomicsContractError(f"{name} exceeds the signed 64-bit range")
    return value


def _require_quality(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitEconomicsContractError(f"{name} must be a non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise UnitEconomicsContractError(
            f"{name} must be a finite non-negative number"
        )
    if number > _MAX_SIGNED_INT64:
        raise UnitEconomicsContractError(
            f"{name} exceeds the signed 64-bit range"
        )
    return number


def _require_unit_id(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or _POSITIVE_DECIMAL_RE.fullmatch(value) is None
        or int(value) > _MAX_SIGNED_INT64
    ):
        raise UnitEconomicsContractError(
            f"{name} must be a canonical positive decimal identifier"
        )
    return value


@dataclass(frozen=True, slots=True)
class UnitEconomicsRow:
    product_numeric_id: int
    revenue: int
    profit: int
    stock_qty: int
    stock_quality: float
    our_price: int
    city_quality: float
    city_price: int
    sales_volume: int
    supply_qty: int
    supply_cost: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_numeric_id",
            _require_int(
                self.product_numeric_id,
                name="product_numeric_id",
                positive=True,
            ),
        )
        for field_name in (
            "revenue",
            "profit",
            "stock_qty",
            "our_price",
            "city_price",
            "sales_volume",
            "supply_qty",
            "supply_cost",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_int(
                    getattr(self, field_name),
                    name=field_name,
                    positive=False,
                ),
            )
        for field_name in _QUALITY_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _require_quality(getattr(self, field_name), name=field_name),
            )


@dataclass(frozen=True, slots=True)
class UnitEconomicsPage:
    unit_id: str
    coverage: str
    rows: tuple[UnitEconomicsRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_unit_id(self.unit_id, name="unit_id"),
        )
        if self.coverage != COVERAGE_OBSERVED:
            raise UnitEconomicsContractError(
                "unit economics coverage must be observed"
            )
        rows = tuple(self.rows)
        if not all(isinstance(item, UnitEconomicsRow) for item in rows):
            raise UnitEconomicsContractError(
                "unit economics rows must be UnitEconomicsRow values"
            )
        if len(rows) > MAX_ROWS:
            raise UnitEconomicsContractError(
                f"unit economics rows exceed the {MAX_ROWS} row limit"
            )
        if len({item.product_numeric_id for item in rows}) != len(rows):
            raise UnitEconomicsContractError(
                "unit economics rows contain duplicate product_numeric_id values"
            )
        object.__setattr__(self, "rows", rows)


def unit_economics_payload(page: UnitEconomicsPage) -> bytes:
    """Serialize one page to the canonical artifact bytes stored in CAS."""

    if not isinstance(page, UnitEconomicsPage):
        raise TypeError("page must be UnitEconomicsPage")
    return canonical_json_bytes(
        {
            "schema": SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "unit_id": page.unit_id,
            "coverage": page.coverage,
            "rows": [
                {field_name: getattr(row, field_name) for field_name in ROW_FIELDS}
                for row in page.rows
            ],
        }
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UnitEconomicsContractError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_unit_economics_payload(raw: bytes) -> UnitEconomicsPage:
    """Validate canonical typed-artifact bytes and return the parsed page."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnitEconomicsContractError(
            "unit economics payload is not valid UTF-8"
        ) from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except UnitEconomicsContractError:
        raise
    except json.JSONDecodeError as exc:
        raise UnitEconomicsContractError(
            "unit economics payload is not valid JSON"
        ) from exc
    except ValueError as exc:
        raise UnitEconomicsContractError(
            "unit economics payload is not valid canonical JSON"
        ) from exc
    if not isinstance(value, dict):
        raise UnitEconomicsContractError(
            "unit economics payload must be a JSON object"
        )
    if set(value) != _TOP_LEVEL_KEYS:
        raise UnitEconomicsContractError(
            "unit economics payload fields do not match the contract"
        )
    if value.get("schema") != SCHEMA:
        raise UnitEconomicsContractError(
            "unsupported unit economics artifact schema"
        )
    contract_version = value.get("contract_version")
    if isinstance(contract_version, bool) or contract_version != CONTRACT_VERSION:
        raise UnitEconomicsContractError(
            "unsupported unit economics contract version"
        )
    coverage = value.get("coverage")
    if coverage != COVERAGE_OBSERVED:
        raise UnitEconomicsContractError(
            "unit economics coverage must be observed"
        )
    raw_rows = value.get("rows")
    if not isinstance(raw_rows, list):
        raise UnitEconomicsContractError("unit economics rows must be an array")
    if len(raw_rows) > MAX_ROWS:
        raise UnitEconomicsContractError(
            f"unit economics rows exceed the {MAX_ROWS} row limit"
        )
    rows: list[UnitEconomicsRow] = []
    for entry in raw_rows:
        if not isinstance(entry, dict) or set(entry) != _ROW_KEYS:
            raise UnitEconomicsContractError(
                "unit economics row fields do not match the contract"
            )
        rows.append(
            UnitEconomicsRow(
                product_numeric_id=entry["product_numeric_id"],
                revenue=entry["revenue"],
                profit=entry["profit"],
                stock_qty=entry["stock_qty"],
                stock_quality=entry["stock_quality"],
                our_price=entry["our_price"],
                city_quality=entry["city_quality"],
                city_price=entry["city_price"],
                sales_volume=entry["sales_volume"],
                supply_qty=entry["supply_qty"],
                supply_cost=entry["supply_cost"],
            )
        )
    page = UnitEconomicsPage(
        unit_id=value.get("unit_id"),
        coverage=coverage,
        rows=tuple(rows),
    )
    if unit_economics_payload(page) != raw:
        raise UnitEconomicsContractError(
            "unit economics payload is not canonically encoded"
        )
    return page


def unit_economics_semantic_fingerprint() -> str:
    """Fingerprint extraction-contract semantics bound into Current State input."""

    return canonical_sha256(
        {
            "schema": SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "response_body_kind": RESPONSE_BODY_KIND,
            "coverage_values": [COVERAGE_OBSERVED],
            "row_fields": list(ROW_FIELDS),
            "integer_fields": list(_INTEGER_FIELDS),
            "quality_fields": list(_QUALITY_FIELDS),
            "max_rows": MAX_ROWS,
        }
    )


__all__ = [
    "CONTRACT_VERSION",
    "COVERAGE_OBSERVED",
    "MAX_ROWS",
    "RESPONSE_BODY_KIND",
    "ROW_FIELDS",
    "SCHEMA",
    "UnitEconomicsContractError",
    "UnitEconomicsPage",
    "UnitEconomicsRow",
    "parse_unit_economics_payload",
    "unit_economics_payload",
    "unit_economics_semantic_fingerprint",
]
