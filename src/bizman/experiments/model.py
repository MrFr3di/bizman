"""Read-only experiment DTOs for verifying uncertain game mechanics.

Invariants
----------
* This package is observation-only: it never performs network calls or game
  writes. Callers supply the before/after goods-page reads that follow an
  explicitly authorized user action.
* One experiment addresses one tracked product, one changed form field and one
  expected direction; deltas are derived only from typed unit-economics values,
  never from prose.
* State identity is a SHA-256 fingerprint, experiment identity is a canonical
  UUIDv7 and time is normalized to RFC3339 UTC with a ``Z`` suffix.
* Every DTO is frozen and slotted and validates in ``__post_init__``; contract
  violations raise :class:`ExperimentError` before inconsistent observations
  can be persisted.
* Delta semantics are pinned by schema ``bizman.experiment-delta.v1`` and the
  module-level fingerprint helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
import re

from bizman.foundation.fingerprint import canonical_sha256
from bizman.foundation.session import new_uuid7
from bizman.foundation.unit_economics import UnitEconomicsPage


TRACKED_FIELDS: tuple[str, ...] = (
    "our_price",
    "supply_qty",
    "supply_cost",
    "sales_volume",
    "stock_qty",
    "stock_quality",
    "city_price",
    "city_quality",
    "profit",
    "revenue",
)
DIRECTIONS = frozenset({"increase", "decrease", "change", "unchanged"})
OUTCOME_STATUSES = frozenset({"confirmed", "refuted", "inconclusive"})

_DELTA_SCHEMA = "bizman.experiment-delta.v1"
_REF_LABELS = frozenset({"before", "after", "control"})
_FLOAT_FIELDS = frozenset({"stock_quality", "city_quality"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_SIGNED_INT64 = (1 << 63) - 1


class ExperimentError(ValueError):
    """An experiment contract violation."""


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ExperimentError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_uuid7(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _UUID7_RE.fullmatch(value) is None:
        raise ExperimentError(f"{name} must be a canonical UUIDv7")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentError(f"{name} must be a non-empty string")
    return value


def _require_entity_id(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise ExperimentError(f"{name} must be a canonical positive decimal identifier")
    return text


def _require_text_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ExperimentError(f"{name} must be a non-empty tuple of strings")
    items = tuple(value)
    if not items or not all(isinstance(item, str) and item for item in items):
        raise ExperimentError(f"{name} must contain non-empty strings")
    return items


def _canonical_instant(value: object, *, name: str) -> tuple[str, datetime]:
    text = _require_text(value, name=name)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ExperimentError(f"{name} must include a timezone offset")
    utc = instant.astimezone(UTC)
    return utc.isoformat().replace("+00:00", "Z"), utc


def _non_negative_int(value: object, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SIGNED_INT64
    ):
        raise ExperimentError(f"{name} must be a non-negative integer")
    return value


def _positive_int64(value: object, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SIGNED_INT64
    ):
        raise ExperimentError(f"{name} must be a positive 64-bit integer")
    return value


def _require_int_value(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentError(f"{name} must be an integer")
    return value


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ExperimentError(f"{name} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class ExperimentHypothesis:
    """The single-product mechanic claim an experiment verifies."""

    hypothesis_id: str
    changed_field: str
    expected_direction: str
    tracked_product_numeric_id: int
    statement: str

    def __post_init__(self) -> None:
        _require_text(self.hypothesis_id, name="hypothesis_id")
        if (
            not isinstance(self.changed_field, str)
            or self.changed_field not in TRACKED_FIELDS
        ):
            raise ExperimentError(
                "changed_field must be a tracked unit-product field"
            )
        if (
            not isinstance(self.expected_direction, str)
            or self.expected_direction not in DIRECTIONS
        ):
            raise ExperimentError(
                "expected_direction must be increase, decrease, change or unchanged"
            )
        _positive_int64(
            self.tracked_product_numeric_id,
            name="tracked_product_numeric_id",
        )
        statement = _require_text(self.statement, name="statement").strip()
        if not statement:
            raise ExperimentError("statement must contain non-whitespace text")
        object.__setattr__(self, "statement", statement)


@dataclass(frozen=True, slots=True)
class ExperimentRef:
    """A state fingerprint plus the captures that observed it."""

    label: str
    state_fingerprint: str
    evidence_refs: tuple[str, ...]
    observed_at: str

    def __post_init__(self) -> None:
        label = _require_text(self.label, name="label")
        if label not in _REF_LABELS:
            raise ExperimentError("label must be before, after or control")
        _require_sha256(self.state_fingerprint, name="state_fingerprint")
        object.__setattr__(
            self,
            "evidence_refs",
            _require_text_tuple(self.evidence_refs, name="evidence_refs"),
        )
        observed, _ = _canonical_instant(self.observed_at, name="observed_at")
        object.__setattr__(self, "observed_at", observed)


@dataclass(frozen=True, slots=True)
class ExperimentAction:
    """The single authorized user action recorded as action metadata."""

    method: str
    route: str
    changed_field: str
    before_value: str
    after_value: str
    status_code: int | None
    location: str | None

    def __post_init__(self) -> None:
        if self.method != "POST":
            raise ExperimentError("method must be POST")
        _require_text(self.route, name="route")
        if (
            not isinstance(self.changed_field, str)
            or self.changed_field not in TRACKED_FIELDS
        ):
            raise ExperimentError(
                "changed_field must be a tracked unit-product field"
            )
        if not isinstance(self.before_value, str) or not isinstance(
            self.after_value, str
        ):
            raise ExperimentError("before_value and after_value must be strings")
        if self.before_value == self.after_value:
            raise ExperimentError("before_value and after_value must differ")
        if self.status_code is not None:
            if (
                isinstance(self.status_code, bool)
                or not isinstance(self.status_code, int)
                or not 100 <= self.status_code <= 599
            ):
                raise ExperimentError(
                    "status_code must be None or an HTTP status code"
                )
        if self.location is not None:
            _require_text(self.location, name="location")


@dataclass(frozen=True, slots=True)
class ControlObservation:
    """An unchanged POST observation that guards against confounders."""

    ref: ExperimentRef
    changed_field: str
    before_value: str
    after_value: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ExperimentRef):
            raise TypeError("ref must be ExperimentRef")
        if self.ref.label != "control":
            raise ExperimentError("control observation ref label must be control")
        if (
            not isinstance(self.changed_field, str)
            or self.changed_field not in TRACKED_FIELDS
        ):
            raise ExperimentError(
                "changed_field must be a tracked unit-product field"
            )
        if not isinstance(self.before_value, str) or not isinstance(
            self.after_value, str
        ):
            raise ExperimentError("before_value and after_value must be strings")
        if self.before_value != self.after_value:
            raise ExperimentError(
                "control before_value and after_value must be equal"
            )


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """One goods-page read bound to its state and evidence refs."""

    ref: ExperimentRef
    goods: UnitEconomicsPage

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ExperimentRef):
            raise TypeError("ref must be ExperimentRef")
        if not isinstance(self.goods, UnitEconomicsPage):
            raise TypeError("goods must be UnitEconomicsPage")


@dataclass(frozen=True, slots=True)
class UnitProductDelta:
    """One tracked field of one unit product before and after an action."""

    unit_id: str
    product_numeric_id: int
    field: str
    before: int | float
    after: int | float
    delta: int | float
    changed: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unit_id",
            _require_entity_id(self.unit_id, name="unit_id"),
        )
        _positive_int64(self.product_numeric_id, name="product_numeric_id")
        if (
            not isinstance(self.field, str)
            or self.field not in TRACKED_FIELDS
        ):
            raise ExperimentError("field must be a tracked unit-product field")
        if not isinstance(self.changed, bool):
            raise ExperimentError("changed must be a bool")
        if self.field in _FLOAT_FIELDS:
            before = _finite_number(self.before, name="before")
            after = _finite_number(self.after, name="after")
            delta = _finite_number(self.delta, name="delta")
            if delta != after - before:
                raise ExperimentError("delta must equal after - before")
            object.__setattr__(self, "before", before)
            object.__setattr__(self, "after", after)
            object.__setattr__(self, "delta", delta)
        else:
            before = _require_int_value(self.before, name="before")
            after = _require_int_value(self.after, name="after")
            delta = _require_int_value(self.delta, name="delta")
            if delta != after - before:
                raise ExperimentError("delta must equal after - before")
        if self.changed is not (self.before != self.after):
            raise ExperimentError(
                "changed must match the before/after comparison"
            )


def _delta_row_semantics(row: UnitProductDelta) -> dict[str, object]:
    return {
        "unit_id": row.unit_id,
        "product_numeric_id": row.product_numeric_id,
        "field": row.field,
        "before": row.before,
        "after": row.after,
        "delta": row.delta,
        "changed": row.changed,
    }


def _observation_delta_fingerprint(
    *,
    from_state_fingerprint: str,
    to_state_fingerprint: str,
    rows: tuple[UnitProductDelta, ...],
) -> str:
    return canonical_sha256(
        {
            "schema": _DELTA_SCHEMA,
            "from_state_fingerprint": from_state_fingerprint,
            "to_state_fingerprint": to_state_fingerprint,
            "rows": [_delta_row_semantics(row) for row in rows],
        }
    )


@dataclass(frozen=True, slots=True)
class ObservationDelta:
    """Deterministic ordered field delta between two goods-page reads."""

    from_ref: ExperimentRef
    to_ref: ExperimentRef
    rows: tuple[UnitProductDelta, ...]
    changed_rows: int
    unchanged_rows: int
    changed_products: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.from_ref, ExperimentRef):
            raise TypeError("from_ref must be ExperimentRef")
        if not isinstance(self.to_ref, ExperimentRef):
            raise TypeError("to_ref must be ExperimentRef")
        rows = tuple(self.rows)
        if not all(isinstance(item, UnitProductDelta) for item in rows):
            raise TypeError("rows must contain only UnitProductDelta values")
        keys = [
            (row.unit_id, row.product_numeric_id, row.field) for row in rows
        ]
        if len(set(keys)) != len(keys):
            raise ExperimentError(
                "rows contain duplicate unit/product/field values"
            )
        if keys != sorted(keys):
            raise ExperimentError(
                "rows must be ordered by unit_id/product_numeric_id/field"
            )
        changed_rows = _non_negative_int(self.changed_rows, name="changed_rows")
        unchanged_rows = _non_negative_int(
            self.unchanged_rows,
            name="unchanged_rows",
        )
        expected_changed = sum(1 for row in rows if row.changed)
        if (
            changed_rows != expected_changed
            or unchanged_rows != len(rows) - expected_changed
        ):
            raise ExperimentError(
                "changed_rows/unchanged_rows disagree with rows"
            )
        changed_products = _non_negative_int(
            self.changed_products,
            name="changed_products",
        )
        expected_products = len(
            {
                (row.unit_id, row.product_numeric_id)
                for row in rows
                if row.changed
            }
        )
        if changed_products != expected_products:
            raise ExperimentError("changed_products disagrees with rows")
        _require_sha256(self.fingerprint, name="fingerprint")
        expected_fingerprint = _observation_delta_fingerprint(
            from_state_fingerprint=self.from_ref.state_fingerprint,
            to_state_fingerprint=self.to_ref.state_fingerprint,
            rows=rows,
        )
        if self.fingerprint != expected_fingerprint:
            raise ExperimentError(
                "observation delta fingerprint is inconsistent"
            )
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """A concluded experiment with its deterministic delta and outcome."""

    experiment_id: str
    hypothesis: ExperimentHypothesis
    action: ExperimentAction
    observations: tuple[ObservationSnapshot, ...]
    primary_delta: ObservationDelta
    consecutive_deltas: tuple[ObservationDelta, ...]
    converged: bool | None
    untouched_products_changed_rows: int
    control_unchanged: bool | None
    status: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_uuid7(self.experiment_id, name="experiment_id")
        if not isinstance(self.hypothesis, ExperimentHypothesis):
            raise TypeError("hypothesis must be ExperimentHypothesis")
        if not isinstance(self.action, ExperimentAction):
            raise TypeError("action must be ExperimentAction")
        observations = tuple(self.observations)
        if not observations:
            raise ExperimentError("observations must not be empty")
        if not all(
            isinstance(item, ObservationSnapshot) for item in observations
        ):
            raise TypeError(
                "observations must contain only ObservationSnapshot values"
            )
        if observations[0].ref.label != "before":
            raise ExperimentError("first observation must be a before read")
        if any(item.ref.label != "after" for item in observations[1:]):
            raise ExperimentError("subsequent observations must be after reads")
        if not isinstance(self.primary_delta, ObservationDelta):
            raise TypeError("primary_delta must be ObservationDelta")
        consecutive = tuple(self.consecutive_deltas)
        if not all(
            isinstance(item, ObservationDelta) for item in consecutive
        ):
            raise TypeError(
                "consecutive_deltas must contain only ObservationDelta values"
            )
        if len(consecutive) != max(len(observations) - 2, 0):
            raise ExperimentError(
                "consecutive_deltas must cover adjacent after reads"
            )
        if len(observations) >= 3:
            if not isinstance(self.converged, bool):
                raise ExperimentError(
                    "converged must be a bool with at least two after reads"
                )
            if self.converged != all(
                item.changed_rows == 0 for item in consecutive
            ):
                raise ExperimentError(
                    "converged disagrees with consecutive deltas"
                )
        elif self.converged is not None:
            raise ExperimentError(
                "converged must be None with fewer than two after reads"
            )
        _non_negative_int(
            self.untouched_products_changed_rows,
            name="untouched_products_changed_rows",
        )
        if self.control_unchanged is not None and not isinstance(
            self.control_unchanged, bool
        ):
            raise ExperimentError("control_unchanged must be None or a bool")
        if (
            not isinstance(self.status, str)
            or self.status not in OUTCOME_STATUSES
        ):
            raise ExperimentError(
                "status must be confirmed, refuted or inconclusive"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            _require_text_tuple(self.evidence_refs, name="evidence_refs"),
        )


def new_experiment_id() -> str:
    """Return a canonical UUIDv7 experiment identifier."""

    return new_uuid7()


__all__ = [
    "ControlObservation",
    "DIRECTIONS",
    "ExperimentAction",
    "ExperimentError",
    "ExperimentHypothesis",
    "ExperimentRef",
    "ExperimentResult",
    "OUTCOME_STATUSES",
    "ObservationDelta",
    "ObservationSnapshot",
    "TRACKED_FIELDS",
    "UnitProductDelta",
    "new_experiment_id",
]
