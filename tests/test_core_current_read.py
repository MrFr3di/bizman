from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
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
    ReplaySession,
    UnitProductState,
    UnitState,
    build_current_snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000221"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


class FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _context(data_dir: Path):
    from bizman.core import CoreContext, RepositoryAssets

    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=FixedClock(),
    )


def _snapshot():
    session = ReplaySession(
        started_at="2026-10-03T10:00:00Z",
        session_id=SESSION_A,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        ended_at="2026-10-03T10:01:00Z",
        status="completed",
        event_count=3,
        last_sequence=2,
    )
    companies = tuple(
        CompanyState(
            company_id=company_id,
            name=f"Company {company_id}",
            source_session_id=SESSION_A,
            source_sequence=sequence,
            observed_at=f"2026-10-03T10:00:0{sequence}Z",
        )
        for sequence, company_id in enumerate(("13393", "13394", "13395"))
    )
    units = (
        UnitState(
            unit_id="33670",
            company_id="13393",
            display_name="Детский магазин",
            city_name="Анкара",
            level=1,
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-10-03T10:00:00Z",
        ),
        UnitState(
            unit_id="33671",
            company_id="13393",
            display_name="Второй магазин",
            city_name="Стамбул",
            level=2,
            source_session_id=SESSION_A,
            source_sequence=1,
            observed_at="2026-10-03T10:00:01Z",
        ),
        UnitState(
            unit_id="33672",
            company_id="13394",
            display_name="Третий магазин",
            city_name="Измир",
            level=1,
            source_session_id=SESSION_A,
            source_sequence=2,
            observed_at="2026-10-03T10:00:02Z",
        ),
    )
    products = (
        ObservedProduct(
            product_numeric_id=101,
            catalog_key="car-seat",
            resolution="resolved",
        ),
        ObservedProduct(
            product_numeric_id=102,
            catalog_key=None,
            resolution="unresolved",
        ),
    )
    unit_products = (
        UnitProductState(
            unit_id="33670",
            product_numeric_id=101,
            revenue=100,
            profit=20,
            stock_qty=5,
            stock_quality=0.5,
            our_price=30,
            city_quality=0.4,
            city_price=35,
            sales_volume=2,
            supply_qty=1,
            supply_cost=10,
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-10-03T10:00:00Z",
        ),
        UnitProductState(
            unit_id="33670",
            product_numeric_id=102,
            revenue=200,
            profit=40,
            stock_qty=6,
            stock_quality=0.6,
            our_price=31,
            city_quality=0.5,
            city_price=36,
            sales_volume=3,
            supply_qty=2,
            supply_cost=11,
            source_session_id=SESSION_A,
            source_sequence=1,
            observed_at="2026-10-03T10:00:01Z",
        ),
        UnitProductState(
            unit_id="33671",
            product_numeric_id=101,
            revenue=300,
            profit=60,
            stock_qty=7,
            stock_quality=0.7,
            our_price=32,
            city_quality=0.6,
            city_price=37,
            sales_volume=4,
            supply_qty=3,
            supply_cost=12,
            source_session_id=SESSION_A,
            source_sequence=2,
            observed_at="2026-10-03T10:00:02Z",
        ),
    )
    return build_current_snapshot(
        CurrentProjectionSpec(
            analysis_profile_sha256=SHA_C,
            unit_economics_contract="core-current-read-fixture",
            catalog_resolver_sha256=SHA_B,
        ),
        (session,),
        companies=companies,
        units=units,
        products=products,
        unit_products=unit_products,
    )


def _write_state(data_dir: Path) -> str:
    path = data_dir / "state" / "current.sqlite3"
    with CurrentStateStore.open_rw(path) as store:
        store.replace_snapshot(_snapshot())
    return path


class CoreCurrentReadDtoTests(unittest.TestCase):
    def test_public_current_read_dtos_are_frozen_slotted_path_free_and_core_owned(self):
        from bizman.core import (
            CurrentCompanyListRequest,
            CurrentCompanyPage,
            CurrentCompanyRecord,
            CurrentProductListRequest,
            CurrentProductPage,
            CurrentProductRecord,
            CurrentStatusRequest,
            CurrentStatusResult,
            CurrentUnitListRequest,
            CurrentUnitPage,
            CurrentUnitRecord,
        )

        dto_types = (
            CurrentCompanyListRequest,
            CurrentCompanyPage,
            CurrentCompanyRecord,
            CurrentProductListRequest,
            CurrentProductPage,
            CurrentProductRecord,
            CurrentStatusRequest,
            CurrentStatusResult,
            CurrentUnitListRequest,
            CurrentUnitPage,
            CurrentUnitRecord,
        )
        for dto in dto_types:
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dto.__dataclass_params__.frozen)
                self.assertIn("__slots__", dto.__dict__)
                self.assertEqual(dto.__module__, "bizman.core.current_read")
                annotations = " ".join(str(field.type) for field in fields(dto))
                self.assertNotIn("Path", annotations)
                self.assertNotIn("sqlite", annotations.casefold())

        status = CurrentStatusResult(
            projection_name="bizman.current",
            projection_version=3,
            analysis_profile_sha256=SHA_A,
            input_fingerprint=SHA_B,
            state_fingerprint=SHA_C,
            status="ready",
            stale_reason=None,
            session_count=0,
            last_session_id=None,
            last_sequence=None,
        )
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            setattr(status, "status", "stale")
        self.assertEqual(tuple(fields(CurrentStatusRequest)), ())

    def test_request_and_page_validation_fails_closed(self):
        from bizman.core import (
            CurrentCompanyListRequest,
            CurrentCompanyPage,
            CurrentCompanyRecord,
            CurrentProductListRequest,
            CurrentUnitListRequest,
        )

        for invalid in (0, 51, -1, True):
            with self.subTest(limit=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    CurrentCompanyListRequest(limit=invalid)
                with self.assertRaises((TypeError, ValueError)):
                    CurrentUnitListRequest(limit=invalid)
                with self.assertRaises((TypeError, ValueError)):
                    CurrentProductListRequest(limit=invalid)

        for invalid in ("013", "abc", "0", 13393, ""):
            with self.subTest(company_id=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    CurrentUnitListRequest(company_id=invalid)
        for invalid in ("013", "abc", "0", 33670, ""):
            with self.subTest(unit_id=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    CurrentProductListRequest(unit_id=invalid)

        with self.assertRaises(ValueError):
            CurrentCompanyListRequest(cursor="not-a-core-cursor")
        with self.assertRaises(ValueError):
            CurrentUnitListRequest(cursor="bmcur1." + "A" * 5000)

        record = CurrentCompanyRecord(
            company_id="13393",
            name="Paradise",
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-10-03T10:00:00Z",
        )
        page = CurrentCompanyPage(items=[record])
        self.assertIsInstance(page.items, tuple)
        self.assertEqual(page.items, (record,))
        self.assertIsNone(page.next_cursor)
        with self.assertRaises((TypeError, ValueError)):
            CurrentCompanyRecord(
                company_id="13393",
                name="   ",
                source_session_id=SESSION_A,
                source_sequence=0,
                observed_at="2026-10-03T10:00:00Z",
            )


class CoreCurrentReadUseCaseTests(unittest.TestCase):
    def test_status_reflects_projection_metadata(self):
        from bizman.core import CurrentStatusRequest, CurrentStatusResult, current_status

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            context = _context(data_dir)
            status = current_status(context, CurrentStatusRequest())

        self.assertIsInstance(status, CurrentStatusResult)
        self.assertEqual(type(status).__module__, "bizman.core.current_read")
        snapshot = _snapshot()
        self.assertEqual(status.projection_name, snapshot.metadata.projection_name)
        self.assertEqual(status.projection_version, snapshot.metadata.projection_version)
        self.assertEqual(status.state_fingerprint, snapshot.metadata.state_fingerprint)
        self.assertEqual(status.input_fingerprint, snapshot.metadata.input_fingerprint)
        self.assertEqual(status.status, "ready")
        self.assertIsNone(status.stale_reason)
        self.assertEqual(status.session_count, 1)
        self.assertEqual(status.last_session_id, SESSION_A)
        self.assertEqual(status.last_sequence, 2)

    def test_company_pagination_round_trips_without_gaps_or_duplicates(self):
        from bizman.core import CurrentCompanyListRequest, list_current_companies

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            context = _context(data_dir)

            first = list_current_companies(context, CurrentCompanyListRequest(limit=2))
            repeat = list_current_companies(context, CurrentCompanyListRequest(limit=2))
            self.assertEqual(first, repeat)
            self.assertIsNotNone(first.next_cursor)
            second = list_current_companies(
                context,
                CurrentCompanyListRequest(limit=2, cursor=first.next_cursor),
            )
            full = list_current_companies(context, CurrentCompanyListRequest(limit=50))

        self.assertEqual(
            [item.company_id for item in first.items],
            ["13393", "13394"],
        )
        self.assertEqual([item.company_id for item in second.items], ["13395"])
        self.assertIsNone(second.next_cursor)
        combined = (*first.items, *second.items)
        self.assertEqual(
            [item.company_id for item in combined],
            [item.company_id for item in full.items],
        )
        self.assertEqual(len({item.company_id for item in combined}), 3)
        self.assertEqual(combined[0].name, "Company 13393")
        self.assertEqual(combined[0].observed_at, "2026-10-03T10:00:00Z")

    def test_unit_and_product_filters_scope_pagination(self):
        from bizman.core import (
            CurrentCompanyListRequest,
            CurrentProductListRequest,
            CurrentUnitListRequest,
            list_current_companies,
            list_current_products,
            list_current_units,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            context = _context(data_dir)

            units = list_current_units(
                context,
                CurrentUnitListRequest(limit=10, company_id="13393"),
            )
            products = list_current_products(
                context,
                CurrentProductListRequest(limit=10, unit_id="33670"),
            )
            unit_page = list_current_units(
                context,
                CurrentUnitListRequest(limit=1, company_id="13393"),
            )
            self.assertIsNotNone(unit_page.next_cursor)
            unit_next = list_current_units(
                context,
                CurrentUnitListRequest(
                    limit=1,
                    company_id="13393",
                    cursor=unit_page.next_cursor,
                ),
            )
            companies = list_current_companies(
                context,
                CurrentCompanyListRequest(limit=1),
            )
            self.assertIsNotNone(companies.next_cursor)

        self.assertEqual([item.unit_id for item in units.items], ["33670", "33671"])
        self.assertEqual(
            [item.product_numeric_id for item in products.items],
            [101, 102],
        )
        self.assertEqual(
            [item.unit_id for item in unit_page.items],
            ["33670"],
        )
        self.assertEqual(
            [item.unit_id for item in unit_next.items],
            ["33671"],
        )
        self.assertIsNone(unit_next.next_cursor)

        with self.assertRaisesRegex(ValueError, "does not belong"):
            CurrentUnitListRequest(
                limit=1,
                company_id="13394",
                cursor=unit_page.next_cursor,
            )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            CurrentUnitListRequest(limit=1, cursor=companies.next_cursor)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            CurrentProductListRequest(limit=1, cursor=companies.next_cursor)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            CurrentCompanyListRequest(limit=1, cursor=unit_page.next_cursor)

    def test_cursor_is_generation_bound_to_state_fingerprint(self):
        from bizman.core import (
            CurrentCompanyListRequest,
            CurrentStatusRequest,
            current_status,
            list_current_companies,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = _write_state(data_dir)
            context = _context(data_dir)
            first = list_current_companies(
                context,
                CurrentCompanyListRequest(limit=1),
            )
            self.assertIsNotNone(first.next_cursor)

            with CurrentStateStore.open_rw(state_path) as store:
                store.mark_stale("synthetic generation change")
            stale = current_status(context, CurrentStatusRequest())
            with self.assertRaisesRegex(ValueError, "cursor generation"):
                list_current_companies(
                    context,
                    CurrentCompanyListRequest(
                        limit=1,
                        cursor=first.next_cursor,
                    ),
                )

        self.assertEqual(stale.status, "stale")
        self.assertEqual(stale.stale_reason, "synthetic generation change")

    def test_missing_unavailable_state_raises_configuration_error(self):
        from bizman.core import (
            ConfigurationError,
            CurrentCompanyListRequest,
            CurrentStatusRequest,
            current_status,
            list_current_companies,
        )

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "missing")
            with self.assertRaisesRegex(ConfigurationError, "Current State is unavailable"):
                current_status(context, CurrentStatusRequest())
            with self.assertRaisesRegex(ConfigurationError, "Current State is unavailable"):
                list_current_companies(context, CurrentCompanyListRequest())

    def test_foreign_schema_raises_contract_mismatch(self):
        from bizman.core import (
            ContractMismatchError,
            CurrentStatusRequest,
            current_status,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            state_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(state_path)
            connection.execute("PRAGMA application_id = 123")
            connection.close()

            with self.assertRaises(ContractMismatchError):
                current_status(_context(data_dir), CurrentStatusRequest())

    def test_tampered_fingerprint_raises_data_integrity(self):
        from bizman.core import (
            CurrentStatusRequest,
            DataIntegrityError,
            current_status,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = _write_state(data_dir)
            connection = sqlite3.connect(state_path)
            connection.execute(
                "UPDATE projection_meta SET state_fingerprint = ?",
                ("0" * 64,),
            )
            connection.commit()
            connection.close()

            with self.assertRaises(DataIntegrityError):
                current_status(_context(data_dir), CurrentStatusRequest())

    def test_bad_request_types_are_rejected_before_storage_access(self):
        from bizman.core import (
            CurrentCompanyListRequest,
            CurrentStatusRequest,
            current_status,
            list_current_companies,
        )

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "missing")
            with self.assertRaises(TypeError):
                current_status(context, CurrentCompanyListRequest())
            with self.assertRaises(TypeError):
                list_current_companies(context, CurrentStatusRequest())


if __name__ == "__main__":
    unittest.main()
