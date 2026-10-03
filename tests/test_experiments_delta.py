from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import re
import unittest

import bizman.experiments as experiments
from bizman.experiments import (
    DIRECTIONS,
    OUTCOME_STATUSES,
    TRACKED_FIELDS,
    ControlObservation,
    ExperimentAction,
    ExperimentError,
    ExperimentHypothesis,
    ExperimentRef,
    ObservationSnapshot,
    UnitProductDelta,
    classify_direction,
    compute_delta,
    evaluate_experiment,
    new_experiment_id,
)
from bizman.foundation.fingerprint import canonical_sha256
from bizman.foundation.unit_economics import UnitEconomicsPage, UnitEconomicsRow


UNIT_ID = "33670"
OTHER_UNIT_ID = "33671"
SHA_A = "0" * 64
SHA_B = "1" * 64
SHA_C = "2" * 64
UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
UUID7 = "01890a5d-ac96-774b-bcce-b302099a8057"
EXPECTED_TRACKED_FIELDS = (
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
EXPECTED_PUBLIC_NAMES = {
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
    "classify_direction",
    "compute_delta",
    "evaluate_experiment",
    "new_experiment_id",
}


def _row(number: int, **overrides: object) -> UnitEconomicsRow:
    values: dict[str, object] = {
        "product_numeric_id": number,
        "revenue": 1000 + number,
        "profit": 100 + number,
        "stock_qty": 10 + number,
        "stock_quality": 3.5,
        "our_price": 150 + number,
        "city_quality": 4.25,
        "city_price": 160 + number,
        "sales_volume": number,
        "supply_qty": 7 + number,
        "supply_cost": 120 + number,
    }
    values.update(overrides)
    return UnitEconomicsRow(**values)


def _page(*rows: UnitEconomicsRow, unit_id: str = UNIT_ID) -> UnitEconomicsPage:
    return UnitEconomicsPage(unit_id=unit_id, coverage="observed", rows=rows)


def _ref(
    label: str = "before",
    *,
    fingerprint: str = SHA_A,
    evidence: tuple[str, ...] = ("cap-a",),
    observed_at: str = "2026-01-01T00:00:00Z",
) -> ExperimentRef:
    return ExperimentRef(
        label=label,
        state_fingerprint=fingerprint,
        evidence_refs=evidence,
        observed_at=observed_at,
    )


def _snapshot(
    label: str,
    rows: tuple[UnitEconomicsRow, ...],
    *,
    fingerprint: str = SHA_A,
    evidence: tuple[str, ...] = ("cap-a",),
    unit_id: str = UNIT_ID,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        ref=_ref(label, fingerprint=fingerprint, evidence=evidence),
        goods=_page(*rows, unit_id=unit_id),
    )


def _hypothesis(**overrides: object) -> ExperimentHypothesis:
    values: dict[str, object] = {
        "hypothesis_id": "bm.exp.0001",
        "changed_field": "our_price",
        "expected_direction": "increase",
        "tracked_product_numeric_id": 11,
        "statement": "Raising our_price increases profit.",
    }
    values.update(overrides)
    return ExperimentHypothesis(**values)


def _action(**overrides: object) -> ExperimentAction:
    values: dict[str, object] = {
        "method": "POST",
        "route": "/shop/goods",
        "changed_field": "our_price",
        "before_value": "161",
        "after_value": "181",
        "status_code": 302,
        "location": "/shop/goods?unit=33670",
    }
    values.update(overrides)
    return ExperimentAction(**values)


def _control(
    *,
    field: str = "supply_qty",
    value: str = "18",
    label: str = "control",
    fingerprint: str = SHA_C,
    evidence: tuple[str, ...] = ("cap-control",),
) -> ControlObservation:
    return ControlObservation(
        ref=_ref(label, fingerprint=fingerprint, evidence=evidence),
        changed_field=field,
        before_value=value,
        after_value=value,
    )


BASE_ROWS = (_row(11), _row(12))
AFTER_PRICE_ROWS = (_row(11, our_price=181), _row(12))
AFTER_STOCK_ROWS = (_row(11, our_price=181), _row(12, stock_qty=999))
AFTER_BOTH_ROWS = (
    _row(11, our_price=181),
    _row(12, stock_qty=999, profit=777),
)


def _expected_fingerprint(
    from_ref: ExperimentRef,
    to_ref: ExperimentRef,
    rows: tuple[UnitProductDelta, ...],
) -> str:
    return canonical_sha256(
        {
            "schema": "bizman.experiment-delta.v1",
            "from_state_fingerprint": from_ref.state_fingerprint,
            "to_state_fingerprint": to_ref.state_fingerprint,
            "rows": [
                {
                    "unit_id": row.unit_id,
                    "product_numeric_id": row.product_numeric_id,
                    "field": row.field,
                    "before": row.before,
                    "after": row.after,
                    "delta": row.delta,
                    "changed": row.changed,
                }
                for row in rows
            ],
        }
    )


class PackageSurfaceTests(unittest.TestCase):
    def test_public_surface_is_alphabetically_sorted_and_complete(self):
        self.assertEqual(experiments.__all__, sorted(experiments.__all__))
        self.assertEqual(set(experiments.__all__), EXPECTED_PUBLIC_NAMES)
        for name in experiments.__all__:
            self.assertIsNotNone(getattr(experiments, name))


class TrackedContractTests(unittest.TestCase):
    def test_tracked_fields_are_frozen(self):
        self.assertEqual(TRACKED_FIELDS, EXPECTED_TRACKED_FIELDS)

    def test_direction_and_outcome_sets_are_frozen(self):
        self.assertEqual(
            DIRECTIONS,
            {"increase", "decrease", "change", "unchanged"},
        )
        self.assertEqual(
            OUTCOME_STATUSES,
            {"confirmed", "refuted", "inconclusive"},
        )


class ExperimentHypothesisTests(unittest.TestCase):
    def test_valid_hypothesis_normalizes_statement(self):
        hypothesis = _hypothesis(statement="  Raising price raises profit.  ")
        self.assertEqual(hypothesis.hypothesis_id, "bm.exp.0001")
        self.assertEqual(hypothesis.changed_field, "our_price")
        self.assertEqual(hypothesis.expected_direction, "increase")
        self.assertEqual(hypothesis.tracked_product_numeric_id, 11)
        self.assertEqual(hypothesis.statement, "Raising price raises profit.")

    def test_invalid_fields_are_rejected(self):
        for overrides in (
            {"hypothesis_id": ""},
            {"changed_field": "price"},
            {"changed_field": None},
            {"expected_direction": "up"},
            {"tracked_product_numeric_id": 0},
            {"tracked_product_numeric_id": -1},
            {"tracked_product_numeric_id": True},
            {"statement": ""},
            {"statement": "   "},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ExperimentError):
                    _hypothesis(**overrides)


class ExperimentRefTests(unittest.TestCase):
    def test_valid_labels_and_instants(self):
        for label in ("before", "after", "control"):
            with self.subTest(label=label):
                ref = _ref(label)
                self.assertEqual(ref.label, label)
                self.assertEqual(ref.observed_at, "2026-01-01T00:00:00Z")
        ref = _ref(observed_at="2026-01-02T03:04:05+00:00")
        self.assertEqual(ref.observed_at, "2026-01-02T03:04:05Z")

    def test_invalid_refs_are_rejected(self):
        cases = (
            {"label": "other"},
            {"fingerprint": "0" * 63},
            {"fingerprint": "A" * 64},
            {"fingerprint": "z" * 64},
            {"evidence": ()},
            {"evidence": ("",)},
            {"evidence": ("cap-a", "")},
            {"evidence": "cap-a"},
            {"observed_at": "2026-01-02T03:04:05"},
            {"observed_at": "not-a-date"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ExperimentError):
                    _ref(**overrides)


class ExperimentActionTests(unittest.TestCase):
    def test_valid_action_without_status_or_location(self):
        action = _action(status_code=None, location=None)
        self.assertEqual(action.method, "POST")
        self.assertEqual(action.before_value, "161")
        self.assertEqual(action.after_value, "181")
        self.assertIsNone(action.status_code)
        self.assertIsNone(action.location)

    def test_invalid_actions_are_rejected(self):
        cases = (
            {"method": "GET"},
            {"route": ""},
            {"changed_field": "price"},
            {"before_value": "161", "after_value": "161"},
            {"before_value": 161},
            {"after_value": None},
            {"status_code": 99},
            {"status_code": 600},
            {"status_code": True},
            {"location": ""},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ExperimentError):
                    _action(**overrides)


class ControlObservationTests(unittest.TestCase):
    def test_valid_control_is_unchanged(self):
        control = _control()
        self.assertEqual(control.ref.label, "control")
        self.assertEqual(control.changed_field, "supply_qty")
        self.assertEqual(control.before_value, control.after_value)

    def test_invalid_controls_are_rejected(self):
        with self.assertRaises(ExperimentError):
            _control(label="before")
        with self.assertRaises(ExperimentError):
            _control(field="price")
        with self.assertRaises(ExperimentError):
            ControlObservation(
                ref=_ref("control"),
                changed_field="supply_qty",
                before_value="18",
                after_value="19",
            )
        with self.assertRaises(TypeError):
            ControlObservation(
                ref="control",
                changed_field="supply_qty",
                before_value="18",
                after_value="18",
            )


class ObservationSnapshotTests(unittest.TestCase):
    def test_valid_snapshot(self):
        snapshot = _snapshot("before", BASE_ROWS)
        self.assertEqual(snapshot.ref.label, "before")
        self.assertEqual(snapshot.goods.unit_id, UNIT_ID)

    def test_snapshot_types_are_enforced(self):
        with self.assertRaises(TypeError):
            ObservationSnapshot(ref="before", goods=_page(*BASE_ROWS))
        with self.assertRaises(TypeError):
            ObservationSnapshot(ref=_ref("before"), goods="page")


class UnitProductDeltaTests(unittest.TestCase):
    def test_int_field_keeps_int_arithmetic(self):
        delta = UnitProductDelta(
            unit_id=UNIT_ID,
            product_numeric_id=11,
            field="our_price",
            before=161,
            after=181,
            delta=20,
            changed=True,
        )
        self.assertIs(type(delta.before), int)
        self.assertIs(type(delta.delta), int)
        self.assertTrue(delta.changed)

    def test_float_field_accepts_int_or_float_and_coerces(self):
        delta = UnitProductDelta(
            unit_id=UNIT_ID,
            product_numeric_id=11,
            field="stock_quality",
            before=3,
            after=4,
            delta=1,
            changed=True,
        )
        self.assertIs(type(delta.before), float)
        self.assertIs(type(delta.after), float)
        self.assertIs(type(delta.delta), float)
        self.assertEqual(delta.delta, 1.0)

    def test_int_float_mismatch_is_rejected(self):
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="our_price",
                before=161.5,
                after=181.5,
                delta=20.0,
                changed=True,
            )
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="stock_quality",
                before="3.5",
                after=4.0,
                delta=0.5,
                changed=True,
            )

    def test_delta_and_changed_must_be_consistent(self):
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="our_price",
                before=161,
                after=181,
                delta=19,
                changed=True,
            )
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="our_price",
                before=161,
                after=181,
                delta=20,
                changed=False,
            )
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="our_price",
                before=161,
                after=161,
                delta=0,
                changed=True,
            )

    def test_invalid_values_are_rejected(self):
        cases = (
            {"field": "price"},
            {"unit_id": "0"},
            {"unit_id": "abc"},
            {"unit_id": ""},
            {"product_numeric_id": 0},
            {"product_numeric_id": True},
            {"before": True},
            {"changed": 1},
            {"delta": 1.5},
        )
        for overrides in cases:
            values: dict[str, object] = {
                "unit_id": UNIT_ID,
                "product_numeric_id": 11,
                "field": "our_price",
                "before": 161,
                "after": 181,
                "delta": 20,
                "changed": True,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(ExperimentError):
                    UnitProductDelta(**values)

    def test_non_finite_float_values_are_rejected(self):
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="stock_quality",
                before=float("nan"),
                after=4.0,
                delta=float("nan"),
                changed=True,
            )
        with self.assertRaises(ExperimentError):
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="city_quality",
                before=4.0,
                after=float("inf"),
                delta=float("inf"),
                changed=True,
            )


class ComputeDeltaTests(unittest.TestCase):
    def test_exact_values_for_one_changed_field(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        delta = compute_delta(before, after)
        self.assertEqual(delta.from_ref, before.ref)
        self.assertEqual(delta.to_ref, after.ref)
        self.assertEqual(len(delta.rows), 20)
        self.assertEqual(delta.changed_rows, 1)
        self.assertEqual(delta.unchanged_rows, 19)
        self.assertEqual(delta.changed_products, 1)
        changed = [row for row in delta.rows if row.changed]
        self.assertEqual(len(changed), 1)
        row = changed[0]
        self.assertEqual(
            (row.unit_id, row.product_numeric_id, row.field),
            (UNIT_ID, 11, "our_price"),
        )
        self.assertEqual((row.before, row.after, row.delta), (161, 181, 20))

    def test_float_field_delta_is_float(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot(
            "after",
            (_row(11, stock_quality=4.0), _row(12)),
            fingerprint=SHA_B,
        )
        delta = compute_delta(before, after)
        changed = [row for row in delta.rows if row.changed]
        self.assertEqual(len(changed), 1)
        row = changed[0]
        self.assertEqual(row.field, "stock_quality")
        self.assertEqual(row.delta, 0.5)
        self.assertIs(type(row.delta), float)

    def test_rows_are_ordered_by_unit_product_field(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        delta = compute_delta(before, after)
        keys = [
            (row.unit_id, row.product_numeric_id, row.field)
            for row in delta.rows
        ]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0], (UNIT_ID, 11, "city_price"))
        self.assertEqual(keys[9], (UNIT_ID, 11, "supply_qty"))
        self.assertEqual(keys[10], (UNIT_ID, 12, "city_price"))
        self.assertEqual(keys[19], (UNIT_ID, 12, "supply_qty"))

    def test_page_row_order_does_not_change_delta(self):
        ordered_before = _snapshot("before", (_row(11), _row(12)))
        shuffled_before = _snapshot("before", (_row(12), _row(11)))
        ordered_after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        shuffled_after = _snapshot(
            "after",
            tuple(reversed(AFTER_PRICE_ROWS)),
            fingerprint=SHA_B,
        )
        first = compute_delta(ordered_before, ordered_after)
        second = compute_delta(shuffled_before, shuffled_after)
        self.assertEqual(first.rows, second.rows)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_fingerprint_matches_pinned_semantics(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        delta = compute_delta(before, after)
        self.assertEqual(
            delta.fingerprint,
            _expected_fingerprint(before.ref, after.ref, delta.rows),
        )

    def test_fingerprint_is_stable_and_changes_with_semantics(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        first = compute_delta(before, after)
        second = compute_delta(before, after)
        self.assertEqual(first.fingerprint, second.fingerprint)
        other_values = compute_delta(
            before,
            _snapshot("after", AFTER_STOCK_ROWS, fingerprint=SHA_B),
        )
        self.assertNotEqual(first.fingerprint, other_values.fingerprint)
        other_ref = compute_delta(
            before,
            _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_C),
        )
        self.assertNotEqual(first.fingerprint, other_ref.fingerprint)

    def test_product_set_must_match_exactly(self):
        before = _snapshot("before", (_row(11), _row(12)))
        with self.assertRaises(ExperimentError):
            compute_delta(before, _snapshot("after", (_row(11),)))
        with self.assertRaises(ExperimentError):
            compute_delta(before, _snapshot("after", (_row(11), _row(13))))

    def test_unit_must_match(self):
        before = _snapshot("before", BASE_ROWS)
        after = _snapshot("after", BASE_ROWS, unit_id=OTHER_UNIT_ID)
        with self.assertRaises(ExperimentError):
            compute_delta(before, after)

    def test_empty_pages_produce_empty_delta(self):
        before = _snapshot("before", ())
        after = _snapshot("after", ())
        delta = compute_delta(before, after)
        self.assertEqual(delta.rows, ())
        self.assertEqual(delta.changed_rows, 0)
        self.assertEqual(delta.unchanged_rows, 0)
        self.assertEqual(delta.changed_products, 0)

    def test_argument_types_are_enforced(self):
        before = _snapshot("before", BASE_ROWS)
        with self.assertRaises(TypeError):
            compute_delta("before", before)
        with self.assertRaises(TypeError):
            compute_delta(before, None)


class ObservationDeltaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        self.after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        self.delta = compute_delta(self.before, self.after)

    def test_derived_counts_are_validated(self):
        with self.assertRaises(ExperimentError):
            replace(self.delta, changed_rows=0)
        with self.assertRaises(ExperimentError):
            replace(self.delta, unchanged_rows=0)
        with self.assertRaises(ExperimentError):
            replace(self.delta, changed_products=0)

    def test_row_order_and_uniqueness_are_enforced(self):
        with self.assertRaises(ExperimentError):
            replace(self.delta, rows=tuple(reversed(self.delta.rows)))
        with self.assertRaises(ExperimentError):
            replace(self.delta, rows=self.delta.rows + (self.delta.rows[0],))

    def test_fingerprint_is_validated(self):
        with self.assertRaises(ExperimentError):
            replace(self.delta, fingerprint=SHA_C)
        with self.assertRaises(ExperimentError):
            replace(self.delta, fingerprint="not-a-fingerprint")

    def test_known_fingerprint_round_trips(self):
        cloned = replace(
            self.delta,
            fingerprint=self.delta.fingerprint,
        )
        self.assertEqual(cloned.fingerprint, self.delta.fingerprint)


class ClassifyDirectionTests(unittest.TestCase):
    def _delta(self, field: str, before: int, after: int, changed: bool):
        return UnitProductDelta(
            unit_id=UNIT_ID,
            product_numeric_id=11,
            field=field,
            before=before,
            after=after,
            delta=after - before,
            changed=changed,
        )

    def test_direction_classification(self):
        unchanged = self._delta("our_price", 161, 161, False)
        increase = self._delta("our_price", 161, 181, True)
        decrease = self._delta("our_price", 181, 161, True)
        self.assertEqual(classify_direction(unchanged), "unchanged")
        self.assertEqual(classify_direction(increase), "increase")
        self.assertEqual(classify_direction(decrease), "decrease")

    def test_float_field_direction(self):
        delta = UnitProductDelta(
            unit_id=UNIT_ID,
            product_numeric_id=11,
            field="stock_quality",
            before=3.5,
            after=4.0,
            delta=0.5,
            changed=True,
        )
        self.assertEqual(classify_direction(delta), "increase")

    def test_non_delta_argument_is_rejected(self):
        with self.assertRaises(TypeError):
            classify_direction("delta")


class EvaluateExperimentTests(unittest.TestCase):
    def _observations(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        return before, after

    def test_confirmed_increase_path(self):
        before, after = self._observations()
        result = evaluate_experiment(_hypothesis(), _action(), (before, after))
        self.assertEqual(result.status, "confirmed")
        self.assertIsNone(result.converged)
        self.assertIsNone(result.control_unchanged)
        self.assertEqual(result.untouched_products_changed_rows, 0)
        self.assertTrue(UUID7_RE.fullmatch(result.experiment_id))
        self.assertEqual(result.primary_delta.from_ref, before.ref)
        self.assertEqual(result.primary_delta.to_ref, after.ref)
        self.assertEqual(result.observations, (before, after))

    def test_provided_experiment_id_is_used(self):
        before, after = self._observations()
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after),
            experiment_id=UUID7,
        )
        self.assertEqual(result.experiment_id, UUID7)

    def test_confirmed_change_path(self):
        before, after = self._observations()
        result = evaluate_experiment(
            _hypothesis(expected_direction="change"),
            _action(),
            (before, after),
        )
        self.assertEqual(result.status, "confirmed")

    def test_refuted_direction_mismatch(self):
        before, after = self._observations()
        result = evaluate_experiment(
            _hypothesis(expected_direction="decrease"),
            _action(),
            (before, after),
        )
        self.assertEqual(result.status, "refuted")

    def test_refuted_when_target_unchanged(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        flat = _snapshot("after", BASE_ROWS, fingerprint=SHA_B)
        result = evaluate_experiment(_hypothesis(), _action(), (before, flat))
        self.assertEqual(result.status, "refuted")

    def test_control_on_same_field_is_inconclusive(self):
        before, after = self._observations()
        control = _control(field="our_price", value="161")
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after),
            control=control,
        )
        self.assertEqual(result.status, "inconclusive")
        self.assertTrue(result.control_unchanged)

    def test_control_on_other_field_keeps_outcome(self):
        before, after = self._observations()
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after),
            control=_control(field="supply_qty", value="18"),
        )
        self.assertEqual(result.status, "confirmed")
        self.assertTrue(result.control_unchanged)

    def test_untouched_products_count_changed_rows(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after = _snapshot("after", AFTER_BOTH_ROWS, fingerprint=SHA_B)
        result = evaluate_experiment(_hypothesis(), _action(), (before, after))
        self.assertEqual(result.primary_delta.changed_rows, 3)
        self.assertEqual(result.primary_delta.changed_products, 2)
        self.assertEqual(result.untouched_products_changed_rows, 2)

    def test_convergence_true_when_after_reads_are_equal(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after_one = _snapshot(
            "after",
            AFTER_PRICE_ROWS,
            fingerprint=SHA_B,
            evidence=("cap-after-1",),
        )
        after_two = _snapshot(
            "after",
            AFTER_PRICE_ROWS,
            fingerprint=SHA_B,
            evidence=("cap-after-2",),
        )
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after_one, after_two),
        )
        self.assertIs(result.converged, True)
        self.assertEqual(len(result.consecutive_deltas), 1)
        self.assertEqual(result.consecutive_deltas[0].changed_rows, 0)

    def test_convergence_false_when_after_reads_differ(self):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after_one = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        after_two = _snapshot("after", AFTER_STOCK_ROWS, fingerprint=SHA_C)
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after_one, after_two),
        )
        self.assertIs(result.converged, False)
        self.assertEqual(result.consecutive_deltas[0].changed_rows, 1)
        changed = [
            (row.product_numeric_id, row.field)
            for row in result.primary_delta.rows
            if row.changed
        ]
        self.assertEqual(changed, [(11, "our_price")])

    def test_evidence_refs_are_deduplicated_in_order(self):
        before = _snapshot(
            "before",
            BASE_ROWS,
            fingerprint=SHA_A,
            evidence=("cap-1", "cap-2"),
        )
        after_one = _snapshot(
            "after",
            AFTER_PRICE_ROWS,
            fingerprint=SHA_B,
            evidence=("cap-2", "cap-3"),
        )
        after_two = _snapshot(
            "after",
            AFTER_PRICE_ROWS,
            fingerprint=SHA_B,
            evidence=("cap-3", "cap-4"),
        )
        control = _control(
            field="our_price",
            value="161",
            evidence=("cap-4", "cap-5"),
        )
        result = evaluate_experiment(
            _hypothesis(),
            _action(),
            (before, after_one, after_two),
            control=control,
        )
        self.assertEqual(
            result.evidence_refs,
            ("cap-1", "cap-2", "cap-3", "cap-4", "cap-5"),
        )

    def test_observation_shape_errors(self):
        before, after = self._observations()
        with self.assertRaises(ExperimentError):
            evaluate_experiment(_hypothesis(), _action(), (before,))
        with self.assertRaises(ExperimentError):
            evaluate_experiment(_hypothesis(), _action(), (after, after))
        with self.assertRaises(ExperimentError):
            evaluate_experiment(_hypothesis(), _action(), (before, before))
        with self.assertRaises(ExperimentError):
            evaluate_experiment(_hypothesis(), _action(), (before, after, before))

    def test_mismatched_action_field_is_rejected(self):
        before, after = self._observations()
        with self.assertRaises(ExperimentError):
            evaluate_experiment(
                _hypothesis(changed_field="supply_qty"),
                _action(),
                (before, after),
            )

    def test_missing_tracked_product_is_rejected(self):
        before, after = self._observations()
        with self.assertRaises(ExperimentError):
            evaluate_experiment(
                _hypothesis(tracked_product_numeric_id=99),
                _action(),
                (before, after),
            )

    def test_argument_types_are_enforced(self):
        before, after = self._observations()
        with self.assertRaises(TypeError):
            evaluate_experiment(object(), _action(), (before, after))
        with self.assertRaises(TypeError):
            evaluate_experiment(_hypothesis(), object(), (before, after))
        with self.assertRaises(TypeError):
            evaluate_experiment(
                _hypothesis(),
                _action(),
                (before, after),
                control="control",
            )
        with self.assertRaises(ExperimentError):
            evaluate_experiment(_hypothesis(), _action(), "not-observations")


class ExperimentResultValidationTests(unittest.TestCase):
    def _evaluate(self, *, after_reads: int = 1):
        before = _snapshot("before", BASE_ROWS, fingerprint=SHA_A)
        after_one = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        observations = [before, after_one]
        if after_reads >= 2:
            observations.append(
                _snapshot(
                    "after",
                    AFTER_PRICE_ROWS,
                    fingerprint=SHA_B,
                    evidence=("cap-after-2",),
                )
            )
        return evaluate_experiment(
            _hypothesis(),
            _action(),
            tuple(observations),
        )

    def test_result_fields_are_consistent(self):
        result = self._evaluate()
        self.assertEqual(result.hypothesis.hypothesis_id, "bm.exp.0001")
        self.assertEqual(result.action.changed_field, "our_price")
        self.assertTrue(result.evidence_refs)

    def test_invalid_results_are_rejected(self):
        result = self._evaluate()
        with self.assertRaises(ExperimentError):
            replace(result, status="maybe")
        with self.assertRaises(ExperimentError):
            replace(result, status=None)
        with self.assertRaises(ExperimentError):
            replace(result, experiment_id="not-a-uuid")
        with self.assertRaises(ExperimentError):
            replace(result, evidence_refs=())
        with self.assertRaises(ExperimentError):
            replace(result, observations=())
        with self.assertRaises(ExperimentError):
            replace(result, observations=(result.observations[1],))
        with self.assertRaises(ExperimentError):
            replace(result, converged=True)
        with self.assertRaises(TypeError):
            replace(result, consecutive_deltas=("delta",))

    def test_two_after_reads_validate_convergence(self):
        result = self._evaluate(after_reads=2)
        self.assertIs(result.converged, True)
        with self.assertRaises(ExperimentError):
            replace(result, consecutive_deltas=())
        with self.assertRaises(ExperimentError):
            replace(result, converged=False)
        with self.assertRaises(ExperimentError):
            replace(result, converged=None)


class FrozenSlotsTests(unittest.TestCase):
    def test_dtos_are_frozen_and_slotted(self):
        before = _snapshot("before", BASE_ROWS)
        after = _snapshot("after", AFTER_PRICE_ROWS, fingerprint=SHA_B)
        delta = compute_delta(before, after)
        result = evaluate_experiment(_hypothesis(), _action(), (before, after))
        values = (
            _ref(),
            _hypothesis(),
            _action(),
            _control(),
            before,
            UnitProductDelta(
                unit_id=UNIT_ID,
                product_numeric_id=11,
                field="our_price",
                before=161,
                after=181,
                delta=20,
                changed=True,
            ),
            delta,
            result,
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            _hypothesis().statement = "mutated"
        with self.assertRaises(FrozenInstanceError):
            delta.changed_rows = 5

    def test_new_experiment_id_is_uuid7(self):
        self.assertTrue(UUID7_RE.fullmatch(new_experiment_id()))


class NoSideEffectImportTests(unittest.TestCase):
    FORBIDDEN_ROOTS = frozenset(
        {
            "aiohttp",
            "ftplib",
            "http",
            "httpx",
            "os",
            "pathlib",
            "requests",
            "shutil",
            "socket",
            "sqlite3",
            "ssl",
            "subprocess",
            "urllib",
        }
    )

    def test_experiments_modules_import_no_filesystem_database_or_network(self):
        package_root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "bizman"
            / "experiments"
        )
        sources = sorted(package_root.glob("*.py"))
        self.assertTrue(sources)
        imported: set[str] = set()
        for path in sources:
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split(".")[0] for alias in node.names
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module
                ):
                    imported.add(node.module.split(".")[0])
        self.assertEqual(imported & self.FORBIDDEN_ROOTS, set())


if __name__ == "__main__":
    unittest.main()
