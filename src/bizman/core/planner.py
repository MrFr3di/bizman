"""Deterministic read-only planner v1 over the Current State read surface.

Documented formula basis
------------------------
The constants used by this module are documented by the captured Wiki topic
«Возможные проблемы магазинов и их решение» (capture ``bizmaniaFAQ.ru.har``,
entry 5608). That topic is the only evidence cited for the constants; no other
source is used and no value is invented when a divisor is missing.

- target stock = ``weeks x sales_volume``; the default is ``weeks=3`` because
  the topic documents the order formula «продажи x3 - остаток» and a normal
  cover range of 2-4 weeks;
- shortfall = ``target_stock - stock_qty - supply_qty`` and the order quantity
  is ``max(0, shortfall)``;
- reference price = ``city_price / city_quality x stock_quality``; the
  recommended price adds a 5% manoeuvre margin because «к цене добавляют
  запас в 5%».

Design notes
------------
* Only the Core Current State read path is used: callers pass a
  :class:`~bizman.core.context.CoreContext` and a request DTO, never a store or
  a filesystem path. The planner reuses the merged read helpers and therefore
  materializes at most one snapshot per call.
* Recommendations come only from unit-products whose ``shop.goods`` surface is
  ``ready``. Products on a ``stale``/``unknown`` surface are never turned into
  recommendations; they are reported separately and bounded by ``limit`` so a
  caller can surface an explicit not-ready marker instead of guessing.
* Ordering is deterministic: expected margin
  ``(our_price - supply_cost) x sales_volume`` descending, then out-of-stock
  risk ``order_qty`` descending, then ``(unit_id, product_numeric_id)``
  ascending.
"""

from __future__ import annotations

from dataclasses import dataclass

from bizman.current import ProductSurfaceState, UnitProductState
from bizman.current.products import SURFACE as _GOODS_SURFACE
from bizman.core.context import CoreContext
from bizman.core.current_read import (
    _current_store,
    _non_negative_int,
    _non_negative_number,
    _positive_int64,
    _require_entity_id,
    _require_instant,
    _require_limit,
    _require_sha256,
    _require_snapshot,
    _require_text,
)

DEFAULT_WEEKS = 3
MIN_WEEKS = 1
MAX_WEEKS = 8
PRICE_MANOEUVRE_MARGIN = 0.05

FLAG_BELOW_CITY = "below_city"
FLAG_ABOVE_CORRIDOR = "above_corridor"
FLAG_POTENTIAL = "potential"
FLAG_OUT_OF_STOCK_RISK = "out_of_stock_risk"
FLAG_UNKNOWN = "unknown"

_FLAG_ORDER: tuple[str, ...] = (
    FLAG_BELOW_CITY,
    FLAG_ABOVE_CORRIDOR,
    FLAG_POTENTIAL,
    FLAG_OUT_OF_STOCK_RISK,
    FLAG_UNKNOWN,
)

WIKI_TOPIC = "Возможные проблемы магазинов и их решение"
WIKI_SOURCE_REF = f"wiki:{WIKI_TOPIC} (bizmaniaFAQ.ru.har#entry-5608)"

_SURFACE_READY = "ready"
_SURFACE_STATUSES = frozenset({"ready", "stale", "unknown"})
_EXCLUDED_SURFACE_STATUSES = frozenset({"stale", "unknown"})


def _require_weeks(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("weeks must be an integer")
    if not MIN_WEEKS <= value <= MAX_WEEKS:
        raise ValueError(f"weeks must be between {MIN_WEEKS} and {MAX_WEEKS}")
    return value


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Bounded planner request; ``unit_id=None`` means all units."""

    weeks: int = DEFAULT_WEEKS
    limit: int = 50
    unit_id: str | None = None

    def __post_init__(self) -> None:
        _require_weeks(self.weeks)
        _require_limit(self.limit)
        if self.unit_id is not None:
            object.__setattr__(
                self,
                "unit_id",
                _require_entity_id(self.unit_id, name="unit_id"),
            )


@dataclass(frozen=True, slots=True)
class PlanRow:
    """One deterministic recommendation row with full provenance."""

    unit_id: str
    product_numeric_id: int
    sales_volume: int
    stock_qty: int
    supply_qty: int
    target_stock: int
    order_qty: int
    our_price: int
    city_price: int
    city_quality: float
    stock_quality: float
    supply_cost: int
    reference_price: float | None
    recommended_price: float | None
    flags: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    observed_at: str
    surface_status: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_entity_id(self.unit_id, name="unit_id"),
        )
        _positive_int64(self.product_numeric_id, name="product_numeric_id")
        for name in (
            "sales_volume",
            "stock_qty",
            "supply_qty",
            "target_stock",
            "order_qty",
            "our_price",
            "city_price",
            "supply_cost",
        ):
            _non_negative_int(getattr(self, name), name=name)
        object.__setattr__(
            self,
            "city_quality",
            _non_negative_number(self.city_quality, name="city_quality"),
        )
        object.__setattr__(
            self,
            "stock_quality",
            _non_negative_number(self.stock_quality, name="stock_quality"),
        )
        for name in ("reference_price", "recommended_price"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _non_negative_number(value, name=name),
                )
        flags = tuple(self.flags)
        if not all(
            isinstance(flag, str) and flag in _FLAG_ORDER for flag in flags
        ):
            raise ValueError("flags must be known planner flag strings")
        if len(set(flags)) != len(flags):
            raise ValueError("flags must not contain duplicates")
        object.__setattr__(self, "flags", flags)
        evidence_refs = tuple(self.evidence_refs)
        if not evidence_refs or not all(
            isinstance(item, str) and item for item in evidence_refs
        ):
            raise ValueError("evidence_refs must contain non-empty strings")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        _require_instant(self.observed_at, name="observed_at")
        if self.surface_status not in _SURFACE_STATUSES:
            raise ValueError("surface_status must be ready, stale or unknown")


@dataclass(frozen=True, slots=True)
class PlanSurfaceExclusion:
    """A unit whose goods surface was not ready for recommendations."""

    unit_id: str
    surface: str
    status: str
    stale_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_entity_id(self.unit_id, name="unit_id"),
        )
        object.__setattr__(
            self,
            "surface",
            _require_text(self.surface, name="surface").strip(),
        )
        if self.status not in _EXCLUDED_SURFACE_STATUSES:
            raise ValueError("excluded surface status must be stale or unknown")
        if self.status == "stale":
            object.__setattr__(
                self,
                "stale_reason",
                _require_text(self.stale_reason, name="stale_reason").strip(),
            )
        elif self.stale_reason is not None:
            raise ValueError("unknown surface cannot carry a stale_reason")


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Deterministic planner result over one Current State snapshot."""

    weeks: int
    rows: tuple[PlanRow, ...]
    generated_from_state_fingerprint: str
    source_refs: tuple[str, ...]
    excluded_surfaces: tuple[PlanSurfaceExclusion, ...] = ()

    def __post_init__(self) -> None:
        _require_weeks(self.weeks)
        rows = tuple(self.rows)
        if not all(isinstance(item, PlanRow) for item in rows):
            raise TypeError("rows must contain only PlanRow values")
        object.__setattr__(self, "rows", rows)
        _require_sha256(
            self.generated_from_state_fingerprint,
            name="generated_from_state_fingerprint",
        )
        source_refs = tuple(self.source_refs)
        if not source_refs or not all(
            isinstance(item, str) and item for item in source_refs
        ):
            raise ValueError("source_refs must contain non-empty strings")
        object.__setattr__(self, "source_refs", source_refs)
        exclusions = tuple(self.excluded_surfaces)
        if not all(
            isinstance(item, PlanSurfaceExclusion) for item in exclusions
        ):
            raise TypeError(
                "excluded_surfaces must contain only PlanSurfaceExclusion values"
            )
        object.__setattr__(self, "excluded_surfaces", exclusions)


def _plan_row(
    value: UnitProductState,
    surface: ProductSurfaceState,
    weeks: int,
) -> PlanRow:
    target_stock = weeks * value.sales_volume
    shortfall = target_stock - value.stock_qty - value.supply_qty
    order_qty = shortfall if shortfall > 0 else 0

    reference_price: float | None
    recommended_price: float | None
    if value.city_quality > 0 and value.stock_quality > 0:
        reference_price = (
            value.city_price / value.city_quality * value.stock_quality
        )
        recommended_price = reference_price * (1.0 + PRICE_MANOEUVRE_MARGIN)
    else:
        reference_price = None
        recommended_price = None

    flags: list[str] = []
    if value.our_price < value.city_price:
        flags.append(FLAG_BELOW_CITY)
    if recommended_price is not None and value.our_price > recommended_price:
        flags.append(FLAG_ABOVE_CORRIDOR)
    if value.our_price < value.city_price and value.profit > 0:
        flags.append(FLAG_POTENTIAL)
    if order_qty > 0:
        flags.append(FLAG_OUT_OF_STOCK_RISK)
    if reference_price is None:
        flags.append(FLAG_UNKNOWN)

    return PlanRow(
        unit_id=value.unit_id,
        product_numeric_id=value.product_numeric_id,
        sales_volume=value.sales_volume,
        stock_qty=value.stock_qty,
        supply_qty=value.supply_qty,
        target_stock=target_stock,
        order_qty=order_qty,
        our_price=value.our_price,
        city_price=value.city_price,
        city_quality=value.city_quality,
        stock_quality=value.stock_quality,
        supply_cost=value.supply_cost,
        reference_price=reference_price,
        recommended_price=recommended_price,
        flags=tuple(flags),
        evidence_refs=(
            f"{value.source_session_id}#seq-{value.source_sequence}",
        ),
        observed_at=value.observed_at,
        surface_status=surface.status,
    )


def _plan_sort_key(row: PlanRow) -> tuple[int, int, str, int]:
    margin = (row.our_price - row.supply_cost) * row.sales_volume
    return (-margin, -row.order_qty, row.unit_id, row.product_numeric_id)


def plan_current(context: CoreContext, request: PlanRequest) -> PlanResult:
    """Plan over the Current State snapshot; one snapshot materialization."""

    if not isinstance(request, PlanRequest):
        raise TypeError("request must be PlanRequest")

    with _current_store(context) as store:
        snapshot = _require_snapshot(store)
        surfaces: dict[str, ProductSurfaceState] = {}
        for surface in snapshot.surfaces:
            if surface.surface != _GOODS_SURFACE:
                continue
            if request.unit_id is not None and surface.unit_id != request.unit_id:
                continue
            surfaces[surface.unit_id] = surface

        rows: list[PlanRow] = []
        for value in snapshot.unit_products:
            if request.unit_id is not None and value.unit_id != request.unit_id:
                continue
            surface = surfaces.get(value.unit_id)
            if surface is None or surface.status != _SURFACE_READY:
                continue
            rows.append(_plan_row(value, surface, request.weeks))

        exclusions = tuple(
            sorted(
                (
                    PlanSurfaceExclusion(
                        unit_id=surface.unit_id,
                        surface=surface.surface,
                        status=surface.status,
                        stale_reason=surface.stale_reason,
                    )
                    for surface in surfaces.values()
                    if surface.status != _SURFACE_READY
                ),
                key=lambda item: item.unit_id,
            )
        )[: request.limit]
        state_fingerprint = snapshot.metadata.state_fingerprint

    rows.sort(key=_plan_sort_key)
    return PlanResult(
        weeks=request.weeks,
        rows=tuple(rows[: request.limit]),
        generated_from_state_fingerprint=state_fingerprint,
        source_refs=(WIKI_SOURCE_REF,),
        excluded_surfaces=exclusions,
    )


__all__ = [
    "PlanRequest",
    "PlanResult",
    "PlanRow",
    "PlanSurfaceExclusion",
    "plan_current",
]
