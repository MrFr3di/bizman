"""Frozen read-only market analytics observations and fingerprints.

The market model mirrors the deterministic DTO style used by the Current State
projection: every value object validates itself in ``__post_init__`` and every
fingerprint is derived from an explicit, order-preserving semantics mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import ClassVar

from bizman.foundation.fingerprint import canonical_sha256


SCHEMA = "bizman.market-observation.v1"
COVERAGE_OBSERVED = "observed"
MAX_ROWS = 512
MAX_HTML_CHARS = 4_194_304
SURFACE_RETAILMARKET_CITY = "retailmarket.city"
SURFACE_RETAILPRICES_GROUP = "retailprices.group"
SURFACE_VENDORS = "vendors"


class MarketContractError(ValueError):
    """A captured market surface cannot be represented by the frozen contract."""


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MarketContractError(f"{name} must be a positive integer")
    return value


def _non_negative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MarketContractError(f"{name} must be a non-negative integer")
    return value


def _finite_number(
    value: object,
    *,
    name: str,
    minimum: float | None = None,
) -> float:
    message = f"{name} must be a finite number"
    if minimum is not None:
        message = f"{name} must be a finite number >= {minimum}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketContractError(message)
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise MarketContractError(message)
    return number


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketContractError(f"{name} must be a non-empty string")
    return value.strip()


def _rows(
    values: object,
    row_type: type,
    *,
    name: str,
    identity,
) -> tuple:
    if not isinstance(values, tuple) or not values:
        raise MarketContractError(
            f"{name} must be a non-empty tuple of {row_type.__name__} values"
        )
    if len(values) > MAX_ROWS:
        raise MarketContractError(f"{name} must not exceed {MAX_ROWS} rows")
    for value in values:
        if not isinstance(value, row_type):
            raise MarketContractError(
                f"{name} must contain only {row_type.__name__} values"
            )
    if len({identity(value) for value in values}) != len(values):
        raise MarketContractError(f"{name} must not repeat a row identity")
    return values


@dataclass(frozen=True, slots=True)
class RetailMarketGroupRow:
    retail_group_id: int
    group_name: str
    sales_volume_rub: int
    delta_percent: float
    share_percent: float
    avg_markup_percent: float
    price_level_percent: float

    def __post_init__(self) -> None:
        _positive_int(self.retail_group_id, name="retail_group_id")
        object.__setattr__(self, "group_name", _text(self.group_name, name="group_name"))
        _non_negative_int(self.sales_volume_rub, name="sales_volume_rub")
        object.__setattr__(
            self,
            "delta_percent",
            _finite_number(self.delta_percent, name="delta_percent"),
        )
        object.__setattr__(
            self,
            "share_percent",
            _finite_number(self.share_percent, name="share_percent", minimum=0.0),
        )
        object.__setattr__(
            self,
            "avg_markup_percent",
            _finite_number(
                self.avg_markup_percent,
                name="avg_markup_percent",
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "price_level_percent",
            _finite_number(
                self.price_level_percent,
                name="price_level_percent",
                minimum=0.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class RetailPriceCityRow:
    city_id: int
    city_name: str
    your_share_percent: float | None
    normalized_price: float
    avg_quality: float

    def __post_init__(self) -> None:
        _positive_int(self.city_id, name="city_id")
        object.__setattr__(self, "city_name", _text(self.city_name, name="city_name"))
        if self.your_share_percent is not None:
            object.__setattr__(
                self,
                "your_share_percent",
                _finite_number(
                    self.your_share_percent,
                    name="your_share_percent",
                    minimum=0.0,
                ),
            )
        object.__setattr__(
            self,
            "normalized_price",
            _finite_number(
                self.normalized_price,
                name="normalized_price",
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "avg_quality",
            _finite_number(self.avg_quality, name="avg_quality", minimum=0.0),
        )


@dataclass(frozen=True, slots=True)
class VendorProductRow:
    product_numeric_id: int
    group_name: str

    def __post_init__(self) -> None:
        _positive_int(self.product_numeric_id, name="product_numeric_id")
        object.__setattr__(self, "group_name", _text(self.group_name, name="group_name"))


@dataclass(frozen=True, slots=True)
class RetailMarketCityPage:
    city_id: int
    rows: tuple[RetailMarketGroupRow, ...]
    surface: ClassVar[str] = SURFACE_RETAILMARKET_CITY
    schema: ClassVar[str] = SCHEMA

    def __post_init__(self) -> None:
        _positive_int(self.city_id, name="city_id")
        _rows(
            self.rows,
            RetailMarketGroupRow,
            name="rows",
            identity=lambda row: row.retail_group_id,
        )


@dataclass(frozen=True, slots=True)
class RetailPriceGroupPage:
    retail_group_id: int
    rows: tuple[RetailPriceCityRow, ...]
    surface: ClassVar[str] = SURFACE_RETAILPRICES_GROUP
    schema: ClassVar[str] = SCHEMA

    def __post_init__(self) -> None:
        _positive_int(self.retail_group_id, name="retail_group_id")
        _rows(
            self.rows,
            RetailPriceCityRow,
            name="rows",
            identity=lambda row: row.city_id,
        )


@dataclass(frozen=True, slots=True)
class VendorCatalogPage:
    rows: tuple[VendorProductRow, ...]
    surface: ClassVar[str] = SURFACE_VENDORS
    schema: ClassVar[str] = SCHEMA

    def __post_init__(self) -> None:
        _rows(
            self.rows,
            VendorProductRow,
            name="rows",
            identity=lambda row: row.product_numeric_id,
        )


def _retail_market_row_semantics(row: RetailMarketGroupRow) -> dict[str, object]:
    return {
        "retail_group_id": row.retail_group_id,
        "group_name": row.group_name,
        "sales_volume_rub": row.sales_volume_rub,
        "delta_percent": row.delta_percent,
        "share_percent": row.share_percent,
        "avg_markup_percent": row.avg_markup_percent,
        "price_level_percent": row.price_level_percent,
    }


def _retail_price_row_semantics(row: RetailPriceCityRow) -> dict[str, object]:
    return {
        "city_id": row.city_id,
        "city_name": row.city_name,
        "your_share_percent": row.your_share_percent,
        "normalized_price": row.normalized_price,
        "avg_quality": row.avg_quality,
    }


def _vendor_row_semantics(row: VendorProductRow) -> dict[str, object]:
    return {
        "product_numeric_id": row.product_numeric_id,
        "group_name": row.group_name,
    }


def market_page_surface(page: object) -> str:
    if isinstance(
        page,
        (RetailMarketCityPage, RetailPriceGroupPage, VendorCatalogPage),
    ):
        return page.surface
    raise TypeError("page must be a market page")


def market_page_semantics(page: object) -> dict[str, object]:
    if isinstance(page, RetailMarketCityPage):
        return {
            "schema": page.schema,
            "surface": page.surface,
            "city_id": page.city_id,
            "rows": [_retail_market_row_semantics(row) for row in page.rows],
        }
    if isinstance(page, RetailPriceGroupPage):
        return {
            "schema": page.schema,
            "surface": page.surface,
            "retail_group_id": page.retail_group_id,
            "rows": [_retail_price_row_semantics(row) for row in page.rows],
        }
    if isinstance(page, VendorCatalogPage):
        return {
            "schema": page.schema,
            "surface": page.surface,
            "rows": [_vendor_row_semantics(row) for row in page.rows],
        }
    raise TypeError("page must be a market page")


def market_page_fingerprint(page: object) -> str:
    return canonical_sha256(market_page_semantics(page))


__all__ = [
    "COVERAGE_OBSERVED",
    "MAX_HTML_CHARS",
    "MAX_ROWS",
    "MarketContractError",
    "RetailMarketCityPage",
    "RetailMarketGroupRow",
    "RetailPriceCityRow",
    "RetailPriceGroupPage",
    "SCHEMA",
    "SURFACE_RETAILMARKET_CITY",
    "SURFACE_RETAILPRICES_GROUP",
    "SURFACE_VENDORS",
    "VendorCatalogPage",
    "VendorProductRow",
    "market_page_fingerprint",
    "market_page_semantics",
    "market_page_surface",
]
