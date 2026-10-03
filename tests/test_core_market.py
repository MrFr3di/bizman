from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.foundation.fingerprint import canonical_sha256
from bizman.market import (
    PROJECTION_SCHEMA,
    MarketObservationRecord,
    MarketStore,
    RetailPriceCityRow,
    RetailPriceGroupPage,
    build_market_projection,
    market_page_semantics,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000201"
SESSION_B = "01991c7d-a400-7000-8000-000000000202"
OBS_A = "01991c7d-a400-7000-8000-000000000301"
OBS_B = "01991c7d-a400-7000-8000-000000000302"
OBS_C = "01991c7d-a400-7000-8000-000000000303"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64

SURFACE_PRICES = "retailprices.group"
SURFACE_CITY = "retailmarket.city"


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


def _page(
    retail_group_id: int = 17,
    city_id: int = 1,
    city_name: str = "Анкара",
) -> RetailPriceGroupPage:
    return RetailPriceGroupPage(
        retail_group_id=retail_group_id,
        rows=(
            RetailPriceCityRow(
                city_id=city_id,
                city_name=city_name,
                your_share_percent=12.5,
                normalized_price=101.5,
                avg_quality=0.75,
            ),
        ),
    )


def _payload(page: RetailPriceGroupPage) -> str:
    return json.dumps(
        market_page_semantics(page),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record(
    *,
    observation_id: str = OBS_A,
    surface: str = SURFACE_PRICES,
    request_key: str = "retailprices.group:17",
    captured_at: str = "2026-10-03T10:00:00Z",
    source_session_id: str = SESSION_A,
    source_sequence: int = 0,
    evidence_ref: str = "synthetic:entry-1",
    response_text_sha256: str = SHA_A,
    artifact_sha256: str = SHA_B,
    payload: str | None = None,
) -> MarketObservationRecord:
    if payload is None:
        payload = _payload(_page())
    return MarketObservationRecord(
        observation_id=observation_id,
        surface=surface,
        request_key=request_key,
        captured_at=captured_at,
        source_session_id=source_session_id,
        source_sequence=source_sequence,
        evidence_ref=evidence_ref,
        response_text_sha256=response_text_sha256,
        artifact_sha256=artifact_sha256,
        payload=payload,
    )


def _write_market(
    data_dir: Path,
    records: tuple[MarketObservationRecord, ...] = (),
) -> Path:
    path = data_dir / "market" / "market.sqlite3"
    with MarketStore.open_rw(path) as store:
        for record in records:
            store.append(record)
    return path


def _empty_fingerprint() -> str:
    return canonical_sha256(
        {
            "schema": PROJECTION_SCHEMA,
            "observation_count": 0,
            "entries": [],
        }
    )


class CoreMarketDtoTests(unittest.TestCase):
    def test_public_market_dtos_are_frozen_slotted_path_free_and_core_owned(self):
        from bizman.core.market import (
            MarketProjectionRequest,
            MarketProjectionResult,
        )

        for dto in (MarketProjectionRequest, MarketProjectionResult):
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dto.__dataclass_params__.frozen)
                self.assertIn("__slots__", dto.__dict__)
                self.assertEqual(dto.__module__, "bizman.core.market")
                annotations = " ".join(str(field.type) for field in fields(dto))
                self.assertNotIn("Path", annotations)
                self.assertNotIn("sqlite", annotations.casefold())

        request = MarketProjectionRequest()
        self.assertIsNone(request.surface)
        self.assertEqual(
            MarketProjectionRequest(surface="vendors").surface,
            "vendors",
        )
        for invalid in ("unknown", "", 17, True):
            with self.subTest(surface=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    MarketProjectionRequest(surface=invalid)

        result = MarketProjectionResult(
            surface=None,
            entry_count=0,
            observation_count=0,
            projection_fingerprint=SHA_C,
            entries=(),
        )
        self.assertEqual(result.entries, ())
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            result.entry_count = 1
        with self.assertRaises((TypeError, ValueError)):
            MarketProjectionResult(
                surface=None,
                entry_count=1,
                observation_count=0,
                projection_fingerprint=SHA_C,
                entries=(),
            )


class CoreMarketProjectionTests(unittest.TestCase):
    def test_projection_returns_latest_entry_and_metadata(self):
        from bizman.core.market import (
            MarketProjectionRequest,
            MarketProjectionResult,
            market_projection,
        )

        older_page = _page(city_name="Анкара")
        newer_page = _page(city_name="Стамбул")
        records = (
            _record(
                observation_id=OBS_A,
                captured_at="2026-10-03T10:00:05Z",
                payload=_payload(older_page),
            ),
            _record(
                observation_id=OBS_B,
                captured_at="2026-10-03T10:00:06Z",
                source_sequence=1,
                payload=_payload(newer_page),
            ),
            _record(
                observation_id=OBS_C,
                surface="vendors",
                request_key="vendors",
                captured_at="2026-10-03T09:00:00Z",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_market(data_dir, records)
            result = market_projection(
                _context(data_dir),
                MarketProjectionRequest(),
            )
        expected = build_market_projection(records)

        self.assertIsInstance(result, MarketProjectionResult)
        self.assertEqual(type(result).__module__, "bizman.core.market")
        self.assertIsNone(result.surface)
        self.assertEqual(result.entry_count, 2)
        self.assertEqual(result.observation_count, 3)
        self.assertEqual(result.entries, expected.entries)
        self.assertEqual(
            result.projection_fingerprint,
            expected.projection_fingerprint,
        )
        prices_entry = result.entries[0]
        self.assertEqual(prices_entry.observation_id, OBS_B)
        self.assertEqual(prices_entry.captured_at, "2026-10-03T10:00:06Z")
        self.assertEqual(prices_entry.payload_json, _payload(newer_page))

    def test_surface_filter_scopes_entries_and_observation_count(self):
        from bizman.core.market import (
            MarketProjectionRequest,
            market_projection,
        )

        records = (
            _record(
                observation_id=OBS_A,
                captured_at="2026-10-03T10:00:00Z",
            ),
            _record(
                observation_id=OBS_B,
                captured_at="2026-10-03T10:00:01Z",
            ),
            _record(
                observation_id=OBS_C,
                surface=SURFACE_CITY,
                request_key="retailmarket.city:1",
                captured_at="2026-10-03T10:00:02Z",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_market(data_dir, records)
            context = _context(data_dir)
            filtered = market_projection(
                context,
                MarketProjectionRequest(surface=SURFACE_PRICES),
            )
            full = market_projection(context, MarketProjectionRequest())

        self.assertEqual(filtered.surface, SURFACE_PRICES)
        self.assertEqual(filtered.entry_count, 1)
        self.assertEqual(filtered.observation_count, 2)
        self.assertEqual(
            [entry.surface for entry in filtered.entries],
            [SURFACE_PRICES],
        )
        self.assertEqual(full.entry_count, 2)
        self.assertEqual(full.observation_count, 3)

    def test_empty_store_returns_empty_projection_with_stable_fingerprint(self):
        from bizman.core.market import MarketProjectionRequest, market_projection

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            _write_market(data_dir)
            first = market_projection(
                _context(data_dir),
                MarketProjectionRequest(),
            )
            second = market_projection(
                _context(data_dir),
                MarketProjectionRequest(),
            )

        self.assertEqual(first.entries, ())
        self.assertEqual(first.entry_count, 0)
        self.assertEqual(first.observation_count, 0)
        self.assertEqual(first.projection_fingerprint, _empty_fingerprint())
        self.assertEqual(first, second)

    def test_missing_store_raises_same_error_as_current_reads(self):
        from bizman.core import (
            ConfigurationError,
            CurrentStatusRequest,
            current_status,
        )
        from bizman.core.market import MarketProjectionRequest, market_projection

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "missing")
            with self.assertRaisesRegex(ConfigurationError, "unavailable"):
                market_projection(context, MarketProjectionRequest())
            with self.assertRaisesRegex(ConfigurationError, "unavailable"):
                current_status(context, CurrentStatusRequest())

    def test_foreign_market_database_raises_contract_mismatch(self):
        from bizman.core import ContractMismatchError
        from bizman.core.market import MarketProjectionRequest, market_projection

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            path = data_dir / "market" / "market.sqlite3"
            path.parent.mkdir(parents=True)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA application_id = 123")
            connection.close()

            with self.assertRaises(ContractMismatchError):
                market_projection(
                    _context(data_dir),
                    MarketProjectionRequest(),
                )

    def test_semantically_tampered_observation_raises_data_integrity(self):
        from bizman.core import DataIntegrityError
        from bizman.core.market import MarketProjectionRequest, market_projection

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            path = _write_market(data_dir, (_record(),))
            connection = sqlite3.connect(path)
            connection.execute(
                """
                INSERT INTO observation(
                    observation_id, surface, request_key, captured_at,
                    source_session_id, source_sequence, evidence_ref,
                    response_text_sha256, artifact_sha256, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "0" * 36,
                    "vendors",
                    "vendors",
                    "not-an-instant",
                    "0" * 36,
                    0,
                    "synthetic:tampered",
                    "0" * 64,
                    "0" * 64,
                    "{}",
                ),
            )
            connection.commit()
            connection.close()

            with self.assertRaises(DataIntegrityError):
                market_projection(
                    _context(data_dir),
                    MarketProjectionRequest(),
                )

    def test_bad_request_types_are_rejected_before_storage_access(self):
        from bizman.core.market import MarketProjectionRequest, market_projection

        with self.assertRaises(TypeError):
            market_projection(None, object())
        with self.assertRaises(TypeError):
            market_projection(object(), MarketProjectionRequest())


if __name__ == "__main__":
    unittest.main()
