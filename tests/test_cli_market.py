from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from bizman.market import (
    MarketObservationRecord,
    MarketStore,
    RetailPriceCityRow,
    RetailPriceGroupPage,
    market_page_semantics,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000401"
OBS_A = "01991c7d-a400-7000-8000-000000000501"
OBS_B = "01991c7d-a400-7000-8000-000000000502"
SHA_A = "a" * 64
SHA_B = "b" * 64

STATUS_KEYS = {
    "available",
    "entry_count",
    "observation_count",
    "projection_fingerprint",
    "surface",
}


def _capture(callable_, *args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = callable_(*args)
    return code, stdout.getvalue(), stderr.getvalue()


def _payload() -> str:
    page = RetailPriceGroupPage(
        retail_group_id=17,
        rows=(
            RetailPriceCityRow(
                city_id=1,
                city_name="Анкара",
                your_share_percent=12.5,
                normalized_price=101.5,
                avg_quality=0.75,
            ),
        ),
    )
    return json.dumps(
        market_page_semantics(page),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record(
    *,
    observation_id: str = OBS_A,
    surface: str = "retailprices.group",
    request_key: str = "retailprices.group:17",
    captured_at: str = "2026-10-03T10:00:00Z",
) -> MarketObservationRecord:
    return MarketObservationRecord(
        observation_id=observation_id,
        surface=surface,
        request_key=request_key,
        captured_at=captured_at,
        source_session_id=SESSION_A,
        source_sequence=0,
        evidence_ref="synthetic:entry-1",
        response_text_sha256=SHA_A,
        artifact_sha256=SHA_B,
        payload=_payload(),
    )


def _write_market(data_dir: Path, records=()) -> None:
    with MarketStore.open_rw(data_dir / "market" / "market.sqlite3") as store:
        for record in records:
            store.append(record)


def _status_argv(data_dir: Path, *extra: str) -> list[str]:
    return [
        "market",
        "status",
        "--repo-root",
        str(REPO_ROOT),
        "--data-dir",
        str(data_dir),
        *extra,
    ]


class MarketCliTests(unittest.TestCase):
    def test_market_status_emits_canonical_json(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_market(
                data_dir,
                (
                    _record(
                        observation_id=OBS_A,
                        captured_at="2026-10-03T10:00:00Z",
                    ),
                    _record(
                        observation_id=OBS_B,
                        captured_at="2026-10-03T10:00:01Z",
                    ),
                ),
            )
            code, stdout, stderr = _capture(main, _status_argv(data_dir))

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertNotIn(", ", stdout)
        self.assertNotIn(": ", stdout)
        payload = json.loads(stdout)
        self.assertEqual(set(payload), STATUS_KEYS)
        self.assertIs(payload["available"], True)
        self.assertIsNone(payload["surface"])
        self.assertEqual(payload["entry_count"], 1)
        self.assertEqual(payload["observation_count"], 2)
        self.assertEqual(len(payload["projection_fingerprint"]), 64)

    def test_market_status_surface_filter_and_invalid_surface(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_market(
                data_dir,
                (
                    _record(observation_id=OBS_A),
                    _record(
                        observation_id=OBS_B,
                        surface="vendors",
                        request_key="vendors",
                    ),
                ),
            )
            filtered = _capture(
                main,
                _status_argv(data_dir, "--surface", "retailprices.group"),
            )
            invalid = _capture(
                main,
                _status_argv(data_dir, "--surface", "unknown"),
            )

        self.assertEqual(filtered[0], 0)
        self.assertEqual(filtered[2], "")
        filtered_payload = json.loads(filtered[1])
        self.assertEqual(
            filtered_payload["surface"],
            "retailprices.group",
        )
        self.assertEqual(filtered_payload["entry_count"], 1)
        self.assertEqual(filtered_payload["observation_count"], 1)

        self.assertEqual(invalid[0], 2)
        self.assertEqual(invalid[1], "")
        self.assertTrue(invalid[2].startswith("ERROR: "))
        self.assertNotIn("Traceback", invalid[2])
        self.assertIn("surface", invalid[2])

    def test_missing_data_dir_exits_non_zero_with_sanitized_error(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "missing"
            code, stdout, stderr = _capture(main, _status_argv(data_dir))

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("ERROR: "))
        self.assertNotIn("Traceback", stderr)
        self.assertIn("unavailable", stderr)

    def test_unknown_or_missing_market_subcommand_is_rejected(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            for argv in (
                ["market", "--repo-root", str(REPO_ROOT), "--data-dir", str(data_dir)],
                _status_argv(data_dir, "--surface"),
                [
                    "market",
                    "entries",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    str(data_dir),
                ],
            ):
                with self.subTest(argv=argv):
                    with self.assertRaises(SystemExit) as raised:
                        _capture(main, argv)
                    self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
