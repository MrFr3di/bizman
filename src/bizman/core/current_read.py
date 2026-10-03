from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import math
import re
import sqlite3

from bizman.core.context import CoreContext
from bizman.core.errors import (
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.core.pagination import decode_cursor, encode_cursor
from bizman.current import (
    CompanyState,
    CurrentStateCompatibilityError,
    CurrentStateIntegrityError,
    CurrentStateMetadata,
    CurrentStateOperationError,
    CurrentStateSnapshot,
    CurrentStateStore,
    UnitProductState,
    UnitState,
)


_MAX_LIMIT = 50
_COMPANIES_KIND = "current-companies"
_UNITS_KIND = "current-units"
_PRODUCTS_KIND = "current-products"
_STATE_UNAVAILABLE = (
    "Current State is unavailable; rebuild it before using Current State reads"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_SIGNED_INT64 = (1 << 63) - 1


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("limit must be an integer")
    if not 1 <= value <= _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_session_id(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SESSION_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical UUIDv7")
    return value


def _require_instant(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return text


def _require_entity_id(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise ValueError(f"{name} must be a canonical positive decimal identifier")
    return text


def _non_negative_int(value: object, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SIGNED_INT64
    ):
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int64(value: object, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SIGNED_INT64
    ):
        raise ValueError(f"{name} must be a positive 64-bit integer")
    return value


def _non_negative_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


@dataclass(frozen=True, slots=True)
class CurrentStatusResult:
    projection_name: str
    projection_version: int
    analysis_profile_sha256: str
    input_fingerprint: str
    state_fingerprint: str
    status: str
    stale_reason: str | None
    session_count: int
    last_session_id: str | None
    last_sequence: int | None

    def __post_init__(self) -> None:
        _require_text(self.projection_name, name="projection_name")
        if (
            isinstance(self.projection_version, bool)
            or not isinstance(self.projection_version, int)
            or self.projection_version <= 0
        ):
            raise ValueError("projection_version must be a positive integer")
        _require_sha256(
            self.analysis_profile_sha256,
            name="analysis_profile_sha256",
        )
        _require_sha256(self.input_fingerprint, name="input_fingerprint")
        _require_sha256(self.state_fingerprint, name="state_fingerprint")
        if self.status not in {"ready", "stale"}:
            raise ValueError("status must be ready or stale")
        if self.stale_reason is not None:
            _require_text(self.stale_reason, name="stale_reason")
        _non_negative_int(self.session_count, name="session_count")
        if self.last_session_id is not None:
            _require_session_id(self.last_session_id, name="last_session_id")
        if self.last_sequence is not None:
            _non_negative_int(self.last_sequence, name="last_sequence")

    @classmethod
    def from_metadata(cls, value: CurrentStateMetadata) -> "CurrentStatusResult":
        if not isinstance(value, CurrentStateMetadata):
            raise TypeError("value must be CurrentStateMetadata")
        return cls(
            projection_name=value.projection_name,
            projection_version=value.projection_version,
            analysis_profile_sha256=value.analysis_profile_sha256,
            input_fingerprint=value.input_fingerprint,
            state_fingerprint=value.state_fingerprint,
            status=value.status,
            stale_reason=value.stale_reason,
            session_count=value.session_count,
            last_session_id=value.last_session_id,
            last_sequence=value.last_sequence,
        )


@dataclass(frozen=True, slots=True)
class CurrentCompanyRecord:
    company_id: str
    name: str
    source_session_id: str
    source_sequence: int
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "company_id",
            _require_entity_id(self.company_id, name="company_id"),
        )
        name = _require_text(self.name, name="name").strip()
        if not name:
            raise ValueError("name must contain non-whitespace text")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "source_session_id",
            _require_session_id(self.source_session_id, name="source_session_id"),
        )
        _non_negative_int(self.source_sequence, name="source_sequence")
        _require_instant(self.observed_at, name="observed_at")


@dataclass(frozen=True, slots=True)
class CurrentUnitRecord:
    unit_id: str
    company_id: str
    display_name: str
    city_name: str
    level: int
    source_session_id: str
    source_sequence: int
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_entity_id(self.unit_id, name="unit_id"),
        )
        object.__setattr__(
            self,
            "company_id",
            _require_entity_id(self.company_id, name="company_id"),
        )
        display_name = _require_text(self.display_name, name="display_name").strip()
        city_name = _require_text(self.city_name, name="city_name").strip()
        if not display_name or not city_name:
            raise ValueError("unit display_name and city_name must contain text")
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "city_name", city_name)
        if (
            isinstance(self.level, bool)
            or not isinstance(self.level, int)
            or self.level <= 0
        ):
            raise ValueError("level must be a positive integer")
        object.__setattr__(
            self,
            "source_session_id",
            _require_session_id(self.source_session_id, name="source_session_id"),
        )
        _non_negative_int(self.source_sequence, name="source_sequence")
        _require_instant(self.observed_at, name="observed_at")


@dataclass(frozen=True, slots=True)
class CurrentProductRecord:
    unit_id: str
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
    source_session_id: str
    source_sequence: int
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_entity_id(self.unit_id, name="unit_id"),
        )
        _positive_int64(self.product_numeric_id, name="product_numeric_id")
        for name in (
            "revenue",
            "profit",
            "stock_qty",
            "our_price",
            "city_price",
            "sales_volume",
            "supply_qty",
            "supply_cost",
        ):
            _non_negative_int(getattr(self, name), name=name)
        object.__setattr__(
            self,
            "stock_quality",
            _non_negative_number(self.stock_quality, name="stock_quality"),
        )
        object.__setattr__(
            self,
            "city_quality",
            _non_negative_number(self.city_quality, name="city_quality"),
        )
        object.__setattr__(
            self,
            "source_session_id",
            _require_session_id(self.source_session_id, name="source_session_id"),
        )
        _non_negative_int(self.source_sequence, name="source_sequence")
        _require_instant(self.observed_at, name="observed_at")


@dataclass(frozen=True, slots=True)
class CurrentStatusRequest:
    """Request Current State projection metadata."""


@dataclass(frozen=True, slots=True)
class CurrentCompanyListRequest:
    limit: int = 10
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        if self.cursor is not None:
            _, company_id = decode_cursor(
                self.cursor,
                kind=_COMPANIES_KIND,
                scope="*",
            )
            _require_entity_id(company_id, name="company cursor company_id")


@dataclass(frozen=True, slots=True)
class CurrentUnitListRequest:
    limit: int = 10
    cursor: str | None = None
    company_id: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        company_id = self.company_id
        if company_id is not None:
            company_id = _require_entity_id(company_id, name="company_id")
            object.__setattr__(self, "company_id", company_id)
        scope = company_id if company_id is not None else "*"
        if self.cursor is not None:
            _, unit_id = decode_cursor(
                self.cursor,
                kind=_UNITS_KIND,
                scope=scope,
            )
            _require_entity_id(unit_id, name="unit cursor unit_id")


@dataclass(frozen=True, slots=True)
class CurrentProductListRequest:
    limit: int = 10
    cursor: str | None = None
    unit_id: str | None = None

    def __post_init__(self) -> None:
        _require_limit(self.limit)
        unit_id = self.unit_id
        if unit_id is not None:
            unit_id = _require_entity_id(unit_id, name="unit_id")
            object.__setattr__(self, "unit_id", unit_id)
        scope = unit_id if unit_id is not None else "*"
        if self.cursor is not None:
            _, cursor_unit_id, product_numeric_id = decode_cursor(
                self.cursor,
                kind=_PRODUCTS_KIND,
                scope=scope,
            )
            _require_entity_id(cursor_unit_id, name="product cursor unit_id")
            _require_entity_id(
                product_numeric_id,
                name="product cursor product_numeric_id",
            )


@dataclass(frozen=True, slots=True)
class CurrentCompanyPage:
    items: tuple[CurrentCompanyRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class CurrentUnitPage:
    items: tuple[CurrentUnitRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class CurrentProductPage:
    items: tuple[CurrentProductRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


def _company_record(value: CompanyState) -> CurrentCompanyRecord:
    return CurrentCompanyRecord(
        company_id=value.company_id,
        name=value.name,
        source_session_id=value.source_session_id,
        source_sequence=value.source_sequence,
        observed_at=value.observed_at,
    )


def _unit_record(value: UnitState) -> CurrentUnitRecord:
    return CurrentUnitRecord(
        unit_id=value.unit_id,
        company_id=value.company_id,
        display_name=value.display_name,
        city_name=value.city_name,
        level=value.level,
        source_session_id=value.source_session_id,
        source_sequence=value.source_sequence,
        observed_at=value.observed_at,
    )


def _product_record(value: UnitProductState) -> CurrentProductRecord:
    return CurrentProductRecord(
        unit_id=value.unit_id,
        product_numeric_id=value.product_numeric_id,
        revenue=value.revenue,
        profit=value.profit,
        stock_qty=value.stock_qty,
        stock_quality=value.stock_quality,
        our_price=value.our_price,
        city_quality=value.city_quality,
        city_price=value.city_price,
        sales_volume=value.sales_volume,
        supply_qty=value.supply_qty,
        supply_cost=value.supply_cost,
        source_session_id=value.source_session_id,
        source_sequence=value.source_sequence,
        observed_at=value.observed_at,
    )


@contextmanager
def _current_store(context: CoreContext) -> Iterator[CurrentStateStore | None]:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    store: CurrentStateStore | None = None
    try:
        store = CurrentStateStore.open_read_only_if_exists(
            context.data_dir / "state" / "current.sqlite3"
        )
        yield store
    except CurrentStateCompatibilityError as exc:
        raise ContractMismatchError("Current State contract is incompatible") from exc
    except CurrentStateIntegrityError as exc:
        raise DataIntegrityError("Current State failed integrity validation") from exc
    except CurrentStateOperationError as exc:
        raise OperationError("Current State read operation failed") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OperationError("Current State read operation failed") from exc
    finally:
        if store is not None:
            store.close()


def _require_snapshot(store: CurrentStateStore | None) -> CurrentStateSnapshot:
    if store is None:
        raise ConfigurationError(_STATE_UNAVAILABLE)
    snapshot = store.snapshot()
    if snapshot is None:
        raise ConfigurationError(_STATE_UNAVAILABLE)
    return snapshot


def current_status(
    context: CoreContext,
    request: CurrentStatusRequest,
) -> CurrentStatusResult:
    if not isinstance(request, CurrentStatusRequest):
        raise TypeError("request must be CurrentStatusRequest")
    with _current_store(context) as store:
        snapshot = _require_snapshot(store)
    return CurrentStatusResult.from_metadata(snapshot.metadata)


def list_current_companies(
    context: CoreContext,
    request: CurrentCompanyListRequest,
) -> CurrentCompanyPage:
    if not isinstance(request, CurrentCompanyListRequest):
        raise TypeError("request must be CurrentCompanyListRequest")
    cursor_state = (
        decode_cursor(request.cursor, kind=_COMPANIES_KIND, scope="*")
        if request.cursor is not None
        else None
    )
    with _current_store(context) as store:
        snapshot = _require_snapshot(store)
        generation = snapshot.metadata.state_fingerprint
        if cursor_state is not None and cursor_state[0] != generation:
            raise ValueError("cursor generation does not match current Current State")
        after = cursor_state[1] if cursor_state is not None else None
        values = tuple(
            item
            for item in snapshot.companies
            if after is None or item.company_id > after
        )
    selected = values[: request.limit + 1]
    items = selected[: request.limit]
    next_cursor = None
    if len(selected) > request.limit and items:
        next_cursor = encode_cursor(
            kind=_COMPANIES_KIND,
            scope="*",
            generation=generation,
            key=(items[-1].company_id,),
        )
    return CurrentCompanyPage(
        items=tuple(_company_record(item) for item in items),
        next_cursor=next_cursor,
    )


def list_current_units(
    context: CoreContext,
    request: CurrentUnitListRequest,
) -> CurrentUnitPage:
    if not isinstance(request, CurrentUnitListRequest):
        raise TypeError("request must be CurrentUnitListRequest")
    scope = request.company_id if request.company_id is not None else "*"
    cursor_state = (
        decode_cursor(request.cursor, kind=_UNITS_KIND, scope=scope)
        if request.cursor is not None
        else None
    )
    with _current_store(context) as store:
        snapshot = _require_snapshot(store)
        generation = snapshot.metadata.state_fingerprint
        if cursor_state is not None and cursor_state[0] != generation:
            raise ValueError("cursor generation does not match current Current State")
        after = cursor_state[1] if cursor_state is not None else None
        values = tuple(
            item
            for item in snapshot.units
            if (request.company_id is None or item.company_id == request.company_id)
            and (after is None or item.unit_id > after)
        )
    selected = values[: request.limit + 1]
    items = selected[: request.limit]
    next_cursor = None
    if len(selected) > request.limit and items:
        next_cursor = encode_cursor(
            kind=_UNITS_KIND,
            scope=scope,
            generation=generation,
            key=(items[-1].unit_id,),
        )
    return CurrentUnitPage(
        items=tuple(_unit_record(item) for item in items),
        next_cursor=next_cursor,
    )


def list_current_products(
    context: CoreContext,
    request: CurrentProductListRequest,
) -> CurrentProductPage:
    if not isinstance(request, CurrentProductListRequest):
        raise TypeError("request must be CurrentProductListRequest")
    scope = request.unit_id if request.unit_id is not None else "*"
    cursor_state = (
        decode_cursor(request.cursor, kind=_PRODUCTS_KIND, scope=scope)
        if request.cursor is not None
        else None
    )
    after: tuple[str, int] | None = None
    if cursor_state is not None:
        if len(cursor_state) != 3:
            raise ValueError("cursor key is invalid")
        after = (
            _require_entity_id(cursor_state[1], name="product cursor unit_id"),
            int(
                _require_entity_id(
                    cursor_state[2],
                    name="product cursor product_numeric_id",
                )
            ),
        )
    with _current_store(context) as store:
        snapshot = _require_snapshot(store)
        generation = snapshot.metadata.state_fingerprint
        if cursor_state is not None and cursor_state[0] != generation:
            raise ValueError("cursor generation does not match current Current State")
        values = tuple(
            item
            for item in snapshot.unit_products
            if (request.unit_id is None or item.unit_id == request.unit_id)
            and (
                after is None
                or (item.unit_id, item.product_numeric_id) > after
            )
        )
    selected = values[: request.limit + 1]
    items = selected[: request.limit]
    next_cursor = None
    if len(selected) > request.limit and items:
        next_cursor = encode_cursor(
            kind=_PRODUCTS_KIND,
            scope=scope,
            generation=generation,
            key=(items[-1].unit_id, str(items[-1].product_numeric_id)),
        )
    return CurrentProductPage(
        items=tuple(_product_record(item) for item in items),
        next_cursor=next_cursor,
    )


__all__ = [
    "CurrentCompanyListRequest",
    "CurrentCompanyPage",
    "CurrentCompanyRecord",
    "CurrentProductListRequest",
    "CurrentProductPage",
    "CurrentProductRecord",
    "CurrentStatusRequest",
    "CurrentStatusResult",
    "CurrentUnitListRequest",
    "CurrentUnitPage",
    "CurrentUnitRecord",
    "current_status",
    "list_current_companies",
    "list_current_products",
    "list_current_units",
]
