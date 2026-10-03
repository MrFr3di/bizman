"""Deterministic before/after deltas and experiment outcome classification.

Invariants
----------
* Deltas compare two :class:`ObservationSnapshot` values that address the same
  unit and exactly the same product set; otherwise the comparison fails closed
  with :class:`ExperimentError`.
* Every tracked field of every product is compared on every delta; rows are
  ordered by ``(unit_id, product_numeric_id, field)`` and the fingerprint uses
  the canonical ``bizman.experiment-delta.v1`` semantics.
* Outcome classification is mechanical: ``refuted``/``confirmed`` follow only
  from the target product-field row and the hypothesis direction, while a
  control that touches the same field makes the experiment ``inconclusive``.
* This module performs no I/O; it never calls the game or the network.
"""

from __future__ import annotations

from bizman.experiments.model import (
    TRACKED_FIELDS,
    ControlObservation,
    ExperimentAction,
    ExperimentError,
    ExperimentHypothesis,
    ExperimentResult,
    ObservationDelta,
    ObservationSnapshot,
    UnitProductDelta,
    _FLOAT_FIELDS,
    _observation_delta_fingerprint,
    new_experiment_id,
)
from bizman.foundation.unit_economics import UnitEconomicsRow


def _row_delta(
    unit_id: str,
    before: UnitEconomicsRow,
    after: UnitEconomicsRow,
    field: str,
) -> UnitProductDelta:
    before_value = getattr(before, field)
    after_value = getattr(after, field)
    if field in _FLOAT_FIELDS:
        return UnitProductDelta(
            unit_id=unit_id,
            product_numeric_id=before.product_numeric_id,
            field=field,
            before=before_value,
            after=after_value,
            delta=float(after_value) - float(before_value),
            changed=before_value != after_value,
        )
    if (
        isinstance(before_value, bool)
        or not isinstance(before_value, int)
        or isinstance(after_value, bool)
        or not isinstance(after_value, int)
    ):
        raise ExperimentError(
            f"{field} must be integer-valued in both observations"
        )
    return UnitProductDelta(
        unit_id=unit_id,
        product_numeric_id=before.product_numeric_id,
        field=field,
        before=before_value,
        after=after_value,
        delta=after_value - before_value,
        changed=before_value != after_value,
    )


def compute_delta(
    before: ObservationSnapshot,
    after: ObservationSnapshot,
) -> ObservationDelta:
    """Compute the deterministic field delta between two goods-page reads."""

    if not isinstance(before, ObservationSnapshot):
        raise TypeError("before must be ObservationSnapshot")
    if not isinstance(after, ObservationSnapshot):
        raise TypeError("after must be ObservationSnapshot")
    if before.goods.unit_id != after.goods.unit_id:
        raise ExperimentError("observation snapshots must share one unit_id")
    before_rows = {row.product_numeric_id: row for row in before.goods.rows}
    after_rows = {row.product_numeric_id: row for row in after.goods.rows}
    if set(before_rows) != set(after_rows):
        raise ExperimentError(
            "observation snapshots must cover the same products"
        )
    rows = tuple(
        _row_delta(
            before.goods.unit_id,
            before_rows[product_numeric_id],
            after_rows[product_numeric_id],
            field,
        )
        for product_numeric_id in sorted(before_rows)
        for field in sorted(TRACKED_FIELDS)
    )
    changed_rows = sum(1 for row in rows if row.changed)
    changed_products = len(
        {(row.unit_id, row.product_numeric_id) for row in rows if row.changed}
    )
    return ObservationDelta(
        from_ref=before.ref,
        to_ref=after.ref,
        rows=rows,
        changed_rows=changed_rows,
        unchanged_rows=len(rows) - changed_rows,
        changed_products=changed_products,
        fingerprint=_observation_delta_fingerprint(
            from_state_fingerprint=before.ref.state_fingerprint,
            to_state_fingerprint=after.ref.state_fingerprint,
            rows=rows,
        ),
    )


def classify_direction(delta: UnitProductDelta) -> str:
    """Classify one delta as unchanged, increase, decrease or change."""

    if not isinstance(delta, UnitProductDelta):
        raise TypeError("delta must be UnitProductDelta")
    if not delta.changed:
        return "unchanged"
    if isinstance(delta.before, str) and isinstance(delta.after, str):
        return "change"
    if isinstance(delta.before, (int, float)) and isinstance(
        delta.after,
        (int, float),
    ):
        if delta.delta > 0:
            return "increase"
        if delta.delta < 0:
            return "decrease"
        return "unchanged"
    raise ExperimentError("cannot classify a delta with mixed value types")


def _outcome(
    hypothesis: ExperimentHypothesis,
    primary_delta: ObservationDelta,
    control: ControlObservation | None,
) -> str:
    if control is not None and control.changed_field == hypothesis.changed_field:
        return "inconclusive"
    target: UnitProductDelta | None = None
    for row in primary_delta.rows:
        if (
            row.product_numeric_id == hypothesis.tracked_product_numeric_id
            and row.field == hypothesis.changed_field
        ):
            target = row
            break
    if target is None:
        raise ExperimentError("primary delta lacks the tracked product field")
    if not target.changed:
        return "refuted"
    if hypothesis.expected_direction == "change":
        return "confirmed"
    if classify_direction(target) == hypothesis.expected_direction:
        return "confirmed"
    return "refuted"


def _collect_evidence_refs(
    observations: tuple[ObservationSnapshot, ...],
    control: ControlObservation | None,
) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for observation in observations:
        for item in observation.ref.evidence_refs:
            if item not in seen:
                seen.add(item)
                refs.append(item)
    if control is not None:
        for item in control.ref.evidence_refs:
            if item not in seen:
                seen.add(item)
                refs.append(item)
    return tuple(refs)


def evaluate_experiment(
    hypothesis: ExperimentHypothesis,
    action: ExperimentAction,
    observations: tuple[ObservationSnapshot, ...],
    *,
    control: ControlObservation | None = None,
    experiment_id: str | None = None,
) -> ExperimentResult:
    """Evaluate one before/after experiment into a deterministic outcome."""

    if not isinstance(hypothesis, ExperimentHypothesis):
        raise TypeError("hypothesis must be ExperimentHypothesis")
    if not isinstance(action, ExperimentAction):
        raise TypeError("action must be ExperimentAction")
    if control is not None and not isinstance(control, ControlObservation):
        raise TypeError("control must be ControlObservation or None")
    values = tuple(observations)
    if len(values) < 2 or not all(
        isinstance(item, ObservationSnapshot) for item in values
    ):
        raise ExperimentError(
            "evaluate requires a before read and at least one after read"
        )
    if values[0].ref.label != "before":
        raise ExperimentError("first observation must be a before read")
    if any(item.ref.label != "after" for item in values[1:]):
        raise ExperimentError("subsequent observations must be after reads")
    if action.changed_field != hypothesis.changed_field:
        raise ExperimentError(
            "action.changed_field must match the hypothesis field"
        )
    before_products = {
        row.product_numeric_id for row in values[0].goods.rows
    }
    if hypothesis.tracked_product_numeric_id not in before_products:
        raise ExperimentError(
            "hypothesis product is absent from the before page"
        )
    primary_delta = compute_delta(values[0], values[1])
    consecutive_deltas = tuple(
        compute_delta(values[index], values[index + 1])
        for index in range(1, len(values) - 1)
    )
    if len(values) >= 3:
        converged: bool | None = all(
            item.changed_rows == 0 for item in consecutive_deltas
        )
    else:
        converged = None
    untouched_products_changed_rows = sum(
        1
        for row in primary_delta.rows
        if row.changed
        and row.product_numeric_id != hypothesis.tracked_product_numeric_id
    )
    control_unchanged = None if control is None else True
    status = _outcome(hypothesis, primary_delta, control)
    if experiment_id is None:
        experiment_id = new_experiment_id()
    return ExperimentResult(
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        action=action,
        observations=values,
        primary_delta=primary_delta,
        consecutive_deltas=consecutive_deltas,
        converged=converged,
        untouched_products_changed_rows=untouched_products_changed_rows,
        control_unchanged=control_unchanged,
        status=status,
        evidence_refs=_collect_evidence_refs(values, control),
    )


__all__ = [
    "classify_direction",
    "compute_delta",
    "evaluate_experiment",
]
