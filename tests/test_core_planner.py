from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.current import (
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateStore,
    ObservedProduct,
    ProductSurfaceState,
    ReplaySession,
    UnitProductState,
    UnitState,
    build_current_snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000321"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
GOODS = "shop.goods"


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value


def _context(data_dir: Path):
    from bizman.core import CoreContext, RepositoryAssets

    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=FixedClock(datetime(2026, 10, 3, 12, 0, tzinfo=UTC)),
    )


def _unit(unit_id: str, level: int) -> UnitState:
    return UnitState(
        unit_id=unit_id,
        company_id="13393",
        display_name=f"Unit {unit_id}",
        city_name=f"City {unit_id}",
        level=level,
        source_session_id=SESSION_A,
        source_sequence=0,
        observed_at="2026-10-03T10:00:00Z",
    )


def _product(
    unit_id: str,
    product_numeric_id: int,
    *,
    revenue: int,
    profit: int,
    stock_qty: int,
    stock_quality: float,
    our_price: int,
    city_quality: float,
    city_price: int,
    sales_volume: int,
    supply_qty: int,
    supply_cost: int,
    source_sequence: int,
) -> UnitProductState:
    return UnitProductState(
        unit_id=unit_id,
        product_numeric_id=product_numeric_id,
        revenue=revenue,
        profit=profit,
        stock_qty=stock_qty,
        stock_quality=stock_quality,
        our_price=our_price,
        city_quality=city_quality,
        city_price=city_price,
        sales_volume=sales_volume,
        supply_qty=supply_qty,
        supply_cost=supply_cost,
        source_session_id=SESSION_A,
        source_sequence=source_sequence,
        observed_at=f"2026-10-03T10:00:{source_sequence:02d}Z",
    )


def _snapshot():
    session = ReplaySession(
        started_at="2026-10-03T10:00:00Z",
        session_id=SESSION_A,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        ended_at="2026-10-03T10:05:00Z",
        status="completed",
        event_count=20,
        last_sequence=19,
    )
    companies = (
        CompanyState(
            company_id="13393",
            name="Synthetic Company",
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-10-03T10:00:00Z",
        ),
    )
    units = (
        _unit("33670", 1),
        _unit("33671", 2),
        _unit("33672", 1),
        _unit("33673", 1),
    )
    products = (
        ObservedProduct(
            product_numeric_id=101,
            catalog_key="bm.product.widget",
            resolution="resolved",
        ),
        ObservedProduct(
            product_numeric_id=102,
            catalog_key=None,
            resolution="unresolved",
        ),
        ObservedProduct(
            product_numeric_id=103,
            catalog_key="bm.product.gadget",
            resolution="resolved",
        ),
    )
    unit_products = (
        # margin (30 - 10) x 2 = 40, order 0
        _product(
            "33670",
            101,
            revenue=100,
            profit=5,
            stock_qty=5,
            stock_quality=0.5,
            our_price=30,
            city_quality=0.5,
            city_price=35,
            sales_volume=2,
            supply_qty=1,
            supply_cost=10,
            source_sequence=0,
        ),
        # margin (50 - 45) x 3 = 15, order 9
        _product(
            "33670",
            102,
            revenue=200,
            profit=0,
            stock_qty=0,
            stock_quality=0.5,
            our_price=50,
            city_quality=0.5,
            city_price=40,
            sales_volume=3,
            supply_qty=0,
            supply_cost=45,
            source_sequence=1,
        ),
        # margin (10 - 5) x 1 = 5, order 1, zero stock quality => unknown
        _product(
            "33670",
            103,
            revenue=50,
            profit=1,
            stock_qty=1,
            stock_quality=0.0,
            our_price=10,
            city_quality=0.4,
            city_price=33,
            sales_volume=1,
            supply_qty=0,
            supply_cost=5,
            source_sequence=2,
        ),
        # margin (20 - 12) x 4 = 32, order 9
        _product(
            "33671",
            101,
            revenue=300,
            profit=3,
            stock_qty=1,
            stock_quality=0.5,
            our_price=20,
            city_quality=0.5,
            city_price=30,
            sales_volume=4,
            supply_qty=2,
            supply_cost=12,
            source_sequence=3,
        ),
        # margin (20 - 12) x 4 = 32, order 0 => second in the tie
        _product(
            "33671",
            102,
            revenue=400,
            profit=0,
            stock_qty=12,
            stock_quality=0.25,
            our_price=20,
            city_quality=0.5,
            city_price=30,
            sales_volume=4,
            supply_qty=0,
            supply_cost=12,
            source_sequence=4,
        ),
        # stale surface: must never become a recommendation
        _product(
            "33672",
            101,
            revenue=999,
            profit=900,
            stock_qty=0,
            stock_quality=1.0,
            our_price=1,
            city_quality=1.0,
            city_price=100,
            sales_volume=10,
            supply_qty=0,
            supply_cost=0,
            source_sequence=5,
        ),
    )
    surfaces = (
        ProductSurfaceState(
            unit_id="33670",
            surface=GOODS,
            status="ready",
            stale_reason=None,
            source_session_id=SESSION_A,
            source_sequence=2,
            observed_at="2026-10-03T10:00:02Z",
        ),
        ProductSurfaceState(
            unit_id="33671",
            surface=GOODS,
            status="ready",
            stale_reason=None,
            source_session_id=SESSION_A,
            source_sequence=4,
            observed_at="2026-10-03T10:00:04Z",
        ),
        ProductSurfaceState(
            unit_id="33672",
            surface=GOODS,
            status="stale",
            stale_reason="unit_economics_parser_v1_incompatible",
            source_session_id=SESSION_A,
            source_sequence=5,
            observed_at="2026-10-03T10:00:05Z",
        ),
        ProductSurfaceState(
            unit_id="33673",
            surface=GOODS,
            status="unknown",
            stale_reason=None,
            source_session_id=None,
            source_sequence=None,
            observed_at=None,
        ),
    )
    return build_current_snapshot(
        CurrentProjectionSpec(
            analysis_profile_sha256=SHA_C,
            unit_economics_contract="core-planner-fixture",
            catalog_resolver_sha256=SHA_B,
        ),
        (session,),
        companies=companies,
        units=units,
        products=products,
        unit_products=unit_products,
        surfaces=surfaces,
    )


def _write_state(data_dir: Path) -> str:
    path = data_dir / "state" / "current.sqlite3"
    with CurrentStateStore.open_rw(path) as store:
        store.replace_snapshot(_snapshot())
    return str(path)


def _row_by_product(result, unit_id: str, product_numeric_id: int):
    for row in result.rows:
        if row.unit_id == unit_id and row.product_numeric_id == product_numeric_id:
            return row
    raise AssertionError(f"missing plan row {unit_id}/{product_numeric_id}")


class PlannerDtoTests(unittest.TestCase):
    def test_plan_dtos_are_frozen_slotted_path_free_and_core_owned(self) -> None:
        from bizman.core import (
            PlanRequest,
            PlanResult,
            PlanRow,
            PlanSurfaceExclusion,
        )

        for dto in (PlanRequest, PlanResult, PlanRow, PlanSurfaceExclusion):
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dto.__dataclass_params__.frozen)
                self.assertIn("__slots__", dto.__dict__)
                self.assertEqual(dto.__module__, "bizman.core.planner")
                annotations = " ".join(str(field.type) for field in fields(dto))
                self.assertNotIn("Path", annotations)
                self.assertNotIn("sqlite", annotations.casefold())

        self.assertEqual(
            tuple(field.name for field in fields(PlanRequest)),
            ("weeks", "limit", "unit_id"),
        )
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            request = PlanRequest()
            request.weeks = 4

    def test_request_validation_fails_closed(self) -> None:
        from bizman.core import PlanRequest

        for weeks in (0, 9, -1):
            with self.subTest(weeks=weeks):
                with self.assertRaises(ValueError):
                    PlanRequest(weeks=weeks)
        for weeks in (True, 1.5, "3"):
            with self.subTest(weeks=weeks):
                with self.assertRaises(TypeError):
                    PlanRequest(weeks=weeks)
        for weeks in (1, 8):
            self.assertEqual(PlanRequest(weeks=weeks).weeks, weeks)

        for limit in (0, 51, -1):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    PlanRequest(limit=limit)
        with self.assertRaises(TypeError):
            PlanRequest(limit=True)

        for unit_id in ("013", "0", "abc", "", 33670):
            with self.subTest(unit_id=unit_id):
                with self.assertRaises((TypeError, ValueError)):
                    PlanRequest(unit_id=unit_id)
        self.assertIsNone(PlanRequest().unit_id)
        self.assertEqual(PlanRequest(unit_id="33670").unit_id, "33670")


class CorePlannerUseCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "BizManData"
        _write_state(self.data_dir)
        self.context = _context(self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_golden_rows_match_documented_formula(self) -> None:
        from bizman.core import PlanRequest, plan_current

        result = plan_current(self.context, PlanRequest())
        expected = {
            ("33670", 101): (6, 0, 35.0, 36.75, ("below_city", "potential"), 0),
            ("33670", 102): (
                9,
                9,
                40.0,
                42.0,
                ("above_corridor", "out_of_stock_risk"),
                1,
            ),
            ("33671", 101): (
                12,
                9,
                30.0,
                31.5,
                ("below_city", "potential", "out_of_stock_risk"),
                3,
            ),
            ("33671", 102): (
                12,
                0,
                15.0,
                15.75,
                ("below_city", "above_corridor"),
                4,
            ),
        }
        for (unit_id, product_numeric_id), values in expected.items():
            with self.subTest(unit_id=unit_id, product=product_numeric_id):
                row = _row_by_product(result, unit_id, product_numeric_id)
                self.assertEqual(row.target_stock, values[0])
                self.assertEqual(row.order_qty, values[1])
                self.assertEqual(row.reference_price, values[2])
                self.assertEqual(row.recommended_price, values[3])
                self.assertEqual(row.flags, values[4])
                self.assertEqual(row.surface_status, "ready")
                self.assertEqual(
                    row.observed_at, f"2026-10-03T10:00:0{values[5]}Z"
                )
                self.assertEqual(
                    row.evidence_refs,
                    (f"{SESSION_A}#seq-{values[5]}",),
                )

    def test_missing_quality_yields_none_and_unknown_flag(self) -> None:
        from bizman.core import PlanRequest, plan_current

        result = plan_current(self.context, PlanRequest())
        row = _row_by_product(result, "33670", 103)
        self.assertIsNone(row.reference_price)
        self.assertIsNone(row.recommended_price)
        self.assertIn("unknown", row.flags)
        self.assertEqual(row.target_stock, 3)
        self.assertEqual(row.order_qty, 2)

    def test_deterministic_order_and_repeatability(self) -> None:
        from bizman.core import PlanRequest, plan_current

        first = plan_current(self.context, PlanRequest())
        second = plan_current(self.context, PlanRequest())
        self.assertEqual(first, second)
        self.assertEqual(
            [(row.unit_id, row.product_numeric_id) for row in first.rows],
            [
                ("33670", 101),
                ("33671", 101),
                ("33671", 102),
                ("33670", 102),
                ("33670", 103),
            ],
        )
        margins = [
            (row.our_price - row.supply_cost) * row.sales_volume
            for row in first.rows
        ]
        self.assertEqual(margins, sorted(margins, reverse=True))

    def test_stale_and_unknown_surfaces_are_excluded_from_recommendations(
        self,
    ) -> None:
        from bizman.core import PlanRequest, plan_current

        result = plan_current(self.context, PlanRequest())
        self.assertNotIn(
            ("33672", 101),
            [(row.unit_id, row.product_numeric_id) for row in result.rows],
        )
        exclusions = {
            (item.unit_id, item.status): item
            for item in result.excluded_surfaces
        }
        self.assertEqual(set(exclusions), {("33672", "stale"), ("33673", "unknown")})
        self.assertEqual(
            exclusions[("33672", "stale")].stale_reason,
            "unit_economics_parser_v1_incompatible",
        )
        self.assertIsNone(exclusions[("33673", "unknown")].stale_reason)

    def test_source_refs_and_provenance(self) -> None:
        from bizman.core import PlanRequest, plan_current

        result = plan_current(self.context, PlanRequest())
        self.assertEqual(result.weeks, 3)
        self.assertEqual(
            result.generated_from_state_fingerprint,
            _snapshot().metadata.state_fingerprint,
        )
        self.assertEqual(len(result.source_refs), 1)
        self.assertIn("Возможные проблемы магазинов и их решение", result.source_refs[0])
        self.assertIn("bizmaniaFAQ.ru.har#entry-5608", result.source_refs[0])
        for row in result.rows:
            self.assertTrue(row.evidence_refs)
            self.assertTrue(row.evidence_refs[0].startswith(SESSION_A + "#seq-"))
            self.assertTrue(row.observed_at.endswith("Z"))

    def test_unit_filter_and_limit_bound_the_result(self) -> None:
        from bizman.core import PlanRequest, plan_current

        filtered = plan_current(self.context, PlanRequest(unit_id="33671"))
        self.assertEqual(
            [(row.unit_id, row.product_numeric_id) for row in filtered.rows],
            [("33671", 101), ("33671", 102)],
        )
        self.assertEqual(filtered.excluded_surfaces, ())

        limited = plan_current(self.context, PlanRequest(limit=2))
        self.assertEqual(
            [(row.unit_id, row.product_numeric_id) for row in limited.rows],
            [("33670", 101), ("33671", 101)],
        )

    def test_request_type_is_checked_before_storage_access(self) -> None:
        from bizman.core import CurrentStatusRequest, plan_current

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "missing")
            with self.assertRaises(TypeError):
                plan_current(context, CurrentStatusRequest())

    def test_unavailable_and_corrupt_state_use_core_errors(self) -> None:
        from bizman.core import (
            ConfigurationError,
            DataIntegrityError,
            PlanRequest,
            plan_current,
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                ConfigurationError, "Current State is unavailable"
            ):
                plan_current(_context(Path(tmp) / "missing"), PlanRequest())

        state_path = self.data_dir / "state" / "current.sqlite3"
        connection = sqlite3.connect(state_path)
        connection.execute(
            "UPDATE projection_meta SET state_fingerprint = ?",
            ("0" * 64,),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataIntegrityError):
            plan_current(self.context, PlanRequest())


if __name__ == "__main__":
    unittest.main()
