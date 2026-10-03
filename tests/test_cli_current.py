from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
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
SESSION_A = "01991c7d-a400-7000-8000-000000000231"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _capture(callable_, *args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = callable_(*args)
    return code, stdout.getvalue(), stderr.getvalue()


def _snapshot():
    session = ReplaySession(
        started_at="2026-10-03T10:00:00Z",
        session_id=SESSION_A,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        ended_at="2026-10-03T10:01:00Z",
        status="completed",
        event_count=2,
        last_sequence=1,
    )
    companies = (
        CompanyState(
            company_id="13393",
            name="Paradise",
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-10-03T10:00:00Z",
        ),
        CompanyState(
            company_id="13394",
            name="Second",
            source_session_id=SESSION_A,
            source_sequence=1,
            observed_at="2026-10-03T10:00:01Z",
        ),
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
            company_id="13394",
            display_name="Второй магазин",
            city_name="Стамбул",
            level=2,
            source_session_id=SESSION_A,
            source_sequence=1,
            observed_at="2026-10-03T10:00:01Z",
        ),
    )
    products = (
        ObservedProduct(
            product_numeric_id=101,
            catalog_key="car-seat",
            resolution="resolved",
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
    )
    return build_current_snapshot(
        CurrentProjectionSpec(
            analysis_profile_sha256=SHA_C,
            unit_economics_contract="cli-current-read-fixture",
            catalog_resolver_sha256=SHA_B,
        ),
        (session,),
        companies=companies,
        units=units,
        products=products,
        unit_products=unit_products,
    )


def _write_state(data_dir: Path) -> None:
    with CurrentStateStore.open_rw(
        data_dir / "state" / "current.sqlite3"
    ) as store:
        store.replace_snapshot(_snapshot())


class CurrentCliTests(unittest.TestCase):
    def test_rebuild_and_status_emit_canonical_json(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            rebuilt = _capture(
                main,
                [
                    "current",
                    "rebuild",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                ],
            )
            status = _capture(
                main,
                [
                    "current",
                    "status",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                ],
            )
            state_exists = (data_dir / "state" / "current.sqlite3").is_file()

        self.assertEqual(rebuilt[0], 0)
        self.assertEqual(status[0], 0)
        self.assertEqual(rebuilt[2], "")
        self.assertEqual(status[2], "")
        for stdout in (rebuilt[1], status[1]):
            self.assertEqual(len(stdout.splitlines()), 1)
            self.assertNotIn(", ", stdout)
            self.assertNotIn(": ", stdout)
        rebuilt_payload = json.loads(rebuilt[1])
        status_payload = json.loads(status[1])
        self.assertEqual(
            set(rebuilt_payload),
            {
                "projection_name",
                "projection_version",
                "analysis_profile_sha256",
                "input_fingerprint",
                "state_fingerprint",
                "status",
                "stale_reason",
                "session_count",
                "last_session_id",
                "last_sequence",
            },
        )
        self.assertEqual(rebuilt_payload["status"], "ready")
        self.assertEqual(status_payload, rebuilt_payload)
        self.assertTrue(state_exists)

    def test_read_commands_emit_items_and_next_cursor_with_filters(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            companies = _capture(
                main,
                [
                    "current",
                    "companies",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                ],
            )
            units = _capture(
                main,
                [
                    "current",
                    "units",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--company",
                    "13393",
                ],
            )
            products = _capture(
                main,
                [
                    "current",
                    "products",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--unit",
                    "33670",
                ],
            )

        for code, stdout, stderr in (companies, units, products):
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(set(payload), {"items", "next_cursor"})
            self.assertIsNone(payload["next_cursor"])
        companies_payload = json.loads(companies[1])
        units_payload = json.loads(units[1])
        products_payload = json.loads(products[1])
        self.assertEqual(
            [item["company_id"] for item in companies_payload["items"]],
            ["13393", "13394"],
        )
        self.assertEqual(
            [item["unit_id"] for item in units_payload["items"]],
            ["33670"],
        )
        self.assertEqual(
            [item["unit_id"] for item in products_payload["items"]],
            ["33670"],
        )
        self.assertEqual(
            products_payload["items"][0]["product_numeric_id"],
            101,
        )
        self.assertEqual(products_payload["items"][0]["stock_quality"], 0.5)

    def test_company_cursor_round_trips_through_cli(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            first = _capture(
                main,
                [
                    "current",
                    "companies",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--limit",
                    "1",
                ],
            )
            first_payload = json.loads(first[1])
            cursor = first_payload["next_cursor"]
            self.assertIsNotNone(cursor)
            second = _capture(
                main,
                [
                    "current",
                    "companies",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--limit",
                    "1",
                    "--cursor",
                    cursor,
                ],
            )

        self.assertEqual(first[0], 0)
        self.assertEqual(second[0], 0)
        self.assertEqual(
            [item["company_id"] for item in first_payload["items"]],
            ["13393"],
        )
        second_payload = json.loads(second[1])
        self.assertEqual(
            [item["company_id"] for item in second_payload["items"]],
            ["13394"],
        )
        self.assertIsNone(second_payload["next_cursor"])

    def test_invalid_requests_exit_two(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_state(data_dir)
            cases = (
                [
                    "current",
                    "companies",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--cursor",
                    "not-a-core-cursor",
                ],
                [
                    "current",
                    "companies",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--limit",
                    "51",
                ],
                [
                    "current",
                    "units",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                    "--company",
                    "013",
                ],
                [
                    "current",
                    "status",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(Path(tmp) / "missing"),
                ],
            )
            results = [_capture(main, case) for case in cases]

        for code, stdout, stderr in results:
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertTrue(stderr.startswith("ERROR: "), stderr)
            self.assertNotIn("Traceback", stderr)
        self.assertIn("Current State is unavailable", results[3][2])

    def test_unknown_subcommand_is_required(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as raised:
                _capture(
                    main,
                    [
                        "current",
                        "--repo-root",
                        str(REPO_ROOT),
                        "--data-dir",
                        str(Path(tmp) / "BizManData"),
                    ],
                )

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
