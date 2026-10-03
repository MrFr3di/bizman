from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bizman.market.store import (
    APPLICATION_ID,
    DEFAULT_READ_LIMIT,
    MAX_READ_LIMIT,
    USER_VERSION,
    MarketObservationRecord,
    MarketStore,
    MarketStoreCompatibilityError,
    MarketStoreConflictError,
    MarketStoreIntegrityError,
)


OBS_A = "01991c7d-a400-7000-8000-000000000101"
OBS_B = "01991c7d-a400-7000-8000-000000000102"
OBS_C = "01991c7d-a400-7000-8000-000000000103"
OBS_D = "01991c7d-a400-7000-8000-000000000104"
SESSION_A = "01991c7d-a400-7000-8000-000000000011"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _record(**overrides: object) -> MarketObservationRecord:
    values: dict[str, object] = {
        "observation_id": OBS_A,
        "surface": "vendors",
        "request_key": "vendors/list",
        "captured_at": "2026-09-07T12:00:00Z",
        "source_session_id": SESSION_A,
        "source_sequence": 0,
        "evidence_ref": "capture:application-events#1",
        "response_text_sha256": SHA_A,
        "artifact_sha256": SHA_B,
        "payload": '{"items":[]}',
    }
    values.update(overrides)
    return MarketObservationRecord(**values)


class MarketObservationRecordTests(unittest.TestCase):
    def test_valid_record_is_utc_canonicalized(self):
        record = _record(captured_at="2026-09-07T14:00:00+02:00")
        self.assertEqual(record.captured_at, "2026-09-07T12:00:00Z")
        self.assertEqual(record.observation_id, OBS_A)
        self.assertEqual(record.surface, "vendors")

    def test_invalid_fields_are_rejected(self):
        cases = (
            ("surface", "retailmarket.unknown"),
            ("surface", ""),
            ("observation_id", "not-a-uuid"),
            ("observation_id", "01991c7d-a400-4000-8000-000000000101"),
            ("observation_id", "01991C7D-A400-7000-8000-000000000101"),
            ("source_session_id", "01991c7d-a400-4000-8000-000000000011"),
            ("captured_at", "2026-09-07T12:00:00"),
            ("captured_at", "not-a-timestamp"),
            ("source_sequence", -1),
            ("source_sequence", True),
            ("request_key", ""),
            ("evidence_ref", ""),
            ("response_text_sha256", "A" * 64),
            ("artifact_sha256", "0" * 63),
            ("payload", ""),
        )
        for name, value in cases:
            with self.subTest(field=name, value=value):
                with self.assertRaises(ValueError):
                    _record(**{name: value})

    def test_record_is_frozen_and_slotted(self):
        record = _record()
        self.assertFalse(hasattr(record, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            record.payload = "{}"


class MarketStoreSchemaTests(unittest.TestCase):
    def test_new_database_bootstraps_identity_and_strict_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market" / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                connection = store._connection
                self.assertEqual(
                    connection.execute("PRAGMA application_id").fetchone()[0],
                    APPLICATION_ID,
                )
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    USER_VERSION,
                )
                self.assertEqual(
                    str(
                        connection.execute("PRAGMA journal_mode").fetchone()[0]
                    ).casefold(),
                    "wal",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA trusted_schema").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    5000,
                )
                strict = {
                    row[1]: row[5]
                    for row in connection.execute("PRAGMA table_list")
                    if row[1] == "observation"
                }
                self.assertEqual(strict, {"observation": 1})
                triggers = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
                    )
                }
                self.assertEqual(len(triggers), 2)

    def test_foreign_non_empty_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foreign.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE garbage(value INTEGER)")
            connection.commit()
            connection.close()
            with self.assertRaises(MarketStoreCompatibilityError):
                MarketStore.open_rw(path)

    def test_wrong_user_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path):
                pass
            connection = sqlite3.connect(path)
            connection.execute(f"PRAGMA user_version = {USER_VERSION + 1}")
            connection.commit()
            connection.close()
            with self.assertRaises(MarketStoreCompatibilityError):
                MarketStore.open_rw(path)


class MarketStoreAppendTests(unittest.TestCase):
    def test_append_read_order_count_and_filters(self):
        first = _record(
            observation_id=OBS_A,
            surface="retailmarket.city",
            request_key="city/1",
            captured_at="2026-09-07T12:00:00Z",
        )
        second = _record(
            observation_id=OBS_B,
            surface="retailmarket.city",
            request_key="city/1",
            captured_at="2026-09-07T12:00:00Z",
            source_sequence=1,
            response_text_sha256=SHA_D,
        )
        later = _record(
            observation_id=OBS_C,
            surface="vendors",
            request_key="vendors/page/3",
            captured_at="2026-09-07T12:01:00Z",
            response_text_sha256=SHA_C,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                store.append(later)
                store.append(first)
                store.append(second)
                self.assertEqual(store.count(), 3)
                self.assertEqual(store.observations(), (first, second, later))
                self.assertEqual(
                    store.observations(surface="retailmarket.city"),
                    (first, second),
                )
                self.assertEqual(
                    store.observations(request_key="vendors/page/3"),
                    (later,),
                )
                self.assertEqual(store.observations(limit=2), (first, second))

    def test_duplicate_content_append_conflicts_and_keeps_count(self):
        record = _record()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                store.append(record)
                duplicate = _record(observation_id=OBS_B, artifact_sha256=SHA_C)
                with self.assertRaises(MarketStoreConflictError):
                    store.append(duplicate)
                self.assertEqual(store.count(), 1)
                self.assertEqual(store.observations(), (record,))

    def test_read_limit_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                with self.assertRaises(ValueError):
                    store.observations(limit=0)
                with self.assertRaises(ValueError):
                    store.observations(limit=MAX_READ_LIMIT + 1)
                with self.assertRaises(TypeError):
                    store.observations(limit=True)
                self.assertEqual(
                    store.observations(limit=DEFAULT_READ_LIMIT),
                    (),
                )


class MarketStoreAppendOnlyTests(unittest.TestCase):
    def test_raw_update_and_delete_are_aborted_and_rows_survive(self):
        record = _record()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                store.append(record)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA trusted_schema = ON")
            try:
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "append-only",
                ):
                    connection.execute(
                        "UPDATE observation SET payload = ?",
                        ('{"changed":true}',),
                    )
                connection.rollback()
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "append-only",
                ):
                    connection.execute("DELETE FROM observation")
                connection.rollback()
                payload = connection.execute(
                    "SELECT payload FROM observation"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(payload, record.payload)
            with MarketStore.open_rw(path) as store:
                self.assertEqual(store.observations(), (record,))
                store.verify_storage_integrity()

    def test_verify_storage_integrity_requires_append_only_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                store.verify_storage_integrity()
                store._connection.execute("DROP TRIGGER observation_no_update")
                with self.assertRaises(MarketStoreIntegrityError):
                    store.verify_storage_integrity()


class MarketStoreReadOnlyTests(unittest.TestCase):
    def test_missing_database_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                MarketStore.open_read_only_if_exists(
                    Path(tmp) / "missing.sqlite3"
                )
            )

    def test_read_only_open_reads_existing_content_and_rejects_writes(self):
        record = _record()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite3"
            with MarketStore.open_rw(path) as store:
                store.append(record)
            with MarketStore.open_read_only_if_exists(path) as store:
                assert store is not None
                self.assertEqual(store.count(), 1)
                self.assertEqual(store.observations(), (record,))
                store.verify_storage_integrity()
                with self.assertRaises(MarketStoreCompatibilityError):
                    store.append(record)
            uri = f"{path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        """
                        INSERT INTO observation VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            OBS_B,
                            "vendors",
                            "vendors/list",
                            "2026-09-07T12:00:00Z",
                            SESSION_A,
                            0,
                            "capture:application-events#2",
                            SHA_A,
                            SHA_B,
                            "{}",
                        ),
                    )
            finally:
                connection.close()


class MarketStoreFingerprintTests(unittest.TestCase):
    def test_fingerprint_deterministic_and_insertion_order_independent(self):
        records = (
            _record(
                observation_id=OBS_A,
                surface="retailmarket.city",
                request_key="city/1",
            ),
            _record(
                observation_id=OBS_B,
                surface="retailprices.group",
                request_key="prices/1",
                response_text_sha256=SHA_C,
            ),
            _record(
                observation_id=OBS_C,
                surface="vendors",
                request_key="vendors/page/1",
                captured_at="2026-09-07T12:02:00Z",
                response_text_sha256=SHA_D,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with MarketStore.open_rw(Path(tmp) / "first.sqlite3") as first:
                for record in records:
                    first.append(record)
                baseline = first.projection_fingerprint()
                first.append(
                    _record(
                        observation_id=OBS_D,
                        surface="retailmarket.city",
                        request_key="city/2",
                        captured_at="2026-09-07T12:03:00Z",
                        response_text_sha256=SHA_B,
                    )
                )
                extended = first.projection_fingerprint()
        with tempfile.TemporaryDirectory() as tmp:
            with MarketStore.open_rw(Path(tmp) / "second.sqlite3") as second:
                for record in reversed(records):
                    second.append(record)
                reordered = second.projection_fingerprint()
        self.assertEqual(baseline, reordered)
        self.assertNotEqual(baseline, extended)

    def test_empty_fingerprint_is_stable_and_well_formed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with MarketStore.open_rw(Path(tmp) / "first.sqlite3") as first:
                first_fingerprint = first.projection_fingerprint()
            with MarketStore.open_rw(Path(tmp) / "second.sqlite3") as second:
                second_fingerprint = second.projection_fingerprint()
        self.assertEqual(first_fingerprint, second_fingerprint)
        self.assertEqual(len(first_fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
