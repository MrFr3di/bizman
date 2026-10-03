from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import json
import random
import unittest

from bizman.foundation.fingerprint import canonical_sha256
from bizman.market.model import MarketContractError
from bizman.market.projection import (
    PROJECTION_SCHEMA,
    MarketProjection,
    MarketProjectionEntry,
    build_market_projection,
    decode_payload,
)
from bizman.market.store import MarketObservationRecord


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64

OBS_A = "01991c7d-a400-7000-8000-000000000001"
OBS_B = "01991c7d-a400-7000-8000-000000000002"
OBS_C = "01991c7d-a400-7000-8000-000000000003"
SESSION_A = "01991c7d-a400-7000-8000-000000000101"
SESSION_B = "01991c7d-a400-7000-8000-000000000102"

KEY_PRICES = "retailprices.group:17"
KEY_VENDORS = "vendors"

PAYLOAD_ONE = '{"v":1}'
PAYLOAD_TWO = '{"v":2}'


def _record(
    *,
    observation_id: str = OBS_A,
    surface: str = "retailprices.group",
    request_key: str = KEY_PRICES,
    captured_at: str = "2026-10-03T10:00:00Z",
    source_session_id: str = SESSION_A,
    source_sequence: int = 0,
    evidence_ref: str = "synthetic:entry-1",
    response_text_sha256: str = SHA_A,
    artifact_sha256: str = SHA_B,
    payload: str = PAYLOAD_ONE,
) -> MarketObservationRecord:
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


def _entry(
    *,
    surface: str = "retailprices.group",
    request_key: str = KEY_PRICES,
    observation_id: str = OBS_A,
    captured_at: str = "2026-10-03T10:00:00Z",
    artifact_sha256: str = SHA_B,
    response_text_sha256: str = SHA_A,
    evidence_ref: str = "synthetic:entry-1",
    payload_json: str = PAYLOAD_ONE,
) -> MarketProjectionEntry:
    return MarketProjectionEntry(
        surface=surface,
        request_key=request_key,
        observation_id=observation_id,
        captured_at=captured_at,
        artifact_sha256=artifact_sha256,
        response_text_sha256=response_text_sha256,
        evidence_ref=evidence_ref,
        payload_json=payload_json,
    )


def _fingerprint(
    entries: tuple[MarketProjectionEntry, ...],
    observation_count: int,
) -> str:
    return canonical_sha256(
        {
            "schema": PROJECTION_SCHEMA,
            "observation_count": observation_count,
            "entries": [
                {
                    "surface": entry.surface,
                    "request_key": entry.request_key,
                    "observation_id": entry.observation_id,
                    "captured_at": entry.captured_at,
                    "artifact_sha256": entry.artifact_sha256,
                    "response_text_sha256": entry.response_text_sha256,
                    "evidence_ref": entry.evidence_ref,
                    "payload_json": entry.payload_json,
                }
                for entry in entries
            ],
        }
    )


def _projection(
    entries: tuple[MarketProjectionEntry, ...],
    observation_count: int,
) -> MarketProjection:
    return MarketProjection(
        entries=entries,
        observation_count=observation_count,
        projection_fingerprint=_fingerprint(entries, observation_count),
    )


class MarketProjectionDtoTests(unittest.TestCase):
    def test_projection_dtos_are_frozen_slotted_and_path_free(self):
        for dto in (MarketProjectionEntry, MarketProjection):
            with self.subTest(dto=dto.__name__):
                self.assertTrue(dto.__dataclass_params__.frozen)
                self.assertIn("__slots__", dto.__dict__)
                self.assertEqual(dto.__module__, "bizman.market.projection")
                annotations = " ".join(str(field.type) for field in fields(dto))
                self.assertNotIn("Path", annotations)
                self.assertNotIn("sqlite", annotations.casefold())

        entry = _entry()
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            entry.captured_at = "2026-10-03T10:00:01Z"
        self.assertEqual(
            [field.name for field in fields(MarketProjectionEntry)],
            [
                "surface",
                "request_key",
                "observation_id",
                "captured_at",
                "artifact_sha256",
                "response_text_sha256",
                "evidence_ref",
                "payload_json",
            ],
        )

    def test_entry_validation_fails_closed_and_normalizes_instants(self):
        for kwargs in (
            {"observation_id": "not-a-uuid"},
            {"captured_at": "2026-10-03T10:00:00"},
            {"captured_at": "not-an-instant"},
            {"artifact_sha256": "xyz"},
            {"response_text_sha256": SHA_A.upper()},
            {"evidence_ref": ""},
            {"payload_json": ""},
            {"request_key": ""},
            {"surface": "unknown"},
            {"surface": ""},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    _entry(**kwargs)

        normalized = _entry(captured_at="2026-10-03T13:00:00+03:00")
        self.assertEqual(normalized.captured_at, "2026-10-03T10:00:00Z")

    def test_duplicate_and_unsorted_entries_are_rejected(self):
        first = _entry(request_key="a", observation_id=OBS_A)
        second = _entry(request_key="b", observation_id=OBS_B)
        duplicate = _entry(request_key="a", observation_id=OBS_B)

        with self.assertRaisesRegex(ValueError, "repeat"):
            _projection((first, duplicate), 2)
        with self.assertRaisesRegex(ValueError, "sorted"):
            _projection((second, first), 2)
        with self.assertRaisesRegex(ValueError, "schema"):
            MarketProjection(
                schema="bizman.other.v1",
                entries=(first,),
                observation_count=1,
                projection_fingerprint=_fingerprint((first,), 1),
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            _projection((), -1)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            MarketProjection(
                entries=(first,),
                observation_count=1,
                projection_fingerprint=SHA_C,
            )
        with self.assertRaises(TypeError):
            MarketProjection(
                entries=(object(),),
                observation_count=1,
                projection_fingerprint=SHA_C,
            )


class BuildMarketProjectionTests(unittest.TestCase):
    def test_latest_observation_wins_per_key(self):
        older = _record(
            captured_at="2026-10-03T10:00:05Z",
            observation_id=OBS_A,
            payload=PAYLOAD_ONE,
        )
        newer = _record(
            captured_at="2026-10-03T10:00:06Z",
            observation_id=OBS_B,
            payload=PAYLOAD_TWO,
        )
        other = _record(
            observation_id=OBS_C,
            surface="vendors",
            request_key=KEY_VENDORS,
            captured_at="2026-10-03T09:00:00Z",
        )

        projection = build_market_projection([newer, other, older])

        self.assertEqual(projection.observation_count, 3)
        self.assertEqual(projection.schema, PROJECTION_SCHEMA)
        self.assertEqual(
            [(entry.surface, entry.request_key) for entry in projection.entries],
            [("retailprices.group", KEY_PRICES), ("vendors", KEY_VENDORS)],
        )
        selected = projection.entries[0]
        self.assertEqual(selected.observation_id, OBS_B)
        self.assertEqual(selected.captured_at, "2026-10-03T10:00:06Z")
        self.assertEqual(selected.payload_json, PAYLOAD_TWO)
        self.assertEqual(
            projection.projection_fingerprint,
            _fingerprint(projection.entries, 3),
        )

    def test_ties_break_by_sequence_then_observation_id(self):
        sequence_loser = _record(
            observation_id=OBS_A,
            source_sequence=0,
        )
        sequence_winner = _record(
            observation_id=OBS_B,
            source_sequence=3,
        )
        id_loser = _record(
            observation_id=OBS_A,
            source_sequence=3,
            source_session_id=SESSION_B,
        )
        id_winner = _record(
            observation_id=OBS_C,
            source_sequence=3,
            source_session_id=SESSION_B,
        )

        projection = build_market_projection(
            [sequence_loser, id_loser, sequence_winner, id_winner]
        )

        self.assertEqual(projection.observation_count, 4)
        self.assertEqual(projection.entries[0].observation_id, OBS_C)

    def test_fingerprint_is_deterministic_under_shuffled_input(self):
        records = [
            _record(
                observation_id=OBS_A,
                captured_at="2026-10-03T10:00:00Z",
                payload=PAYLOAD_ONE,
            ),
            _record(
                observation_id=OBS_B,
                captured_at="2026-10-03T10:00:01Z",
                payload=PAYLOAD_TWO,
            ),
            _record(
                observation_id=OBS_C,
                surface="vendors",
                request_key=KEY_VENDORS,
                captured_at="2026-10-03T10:00:02Z",
            ),
            _record(
                observation_id="01991c7d-a400-7000-8000-000000000004",
                surface="retailmarket.city",
                request_key="retailmarket.city:1",
                captured_at="2026-10-03T10:00:03Z",
            ),
            _record(
                observation_id="01991c7d-a400-7000-8000-000000000005",
                surface="retailmarket.city",
                request_key="retailmarket.city:1",
                captured_at="2026-10-03T10:00:04Z",
            ),
        ]
        expected = build_market_projection(records)
        shuffled = list(records)
        random.Random(20261003).shuffle(shuffled)
        reordered = build_market_projection(shuffled)
        reversed_order = build_market_projection(reversed(records))

        self.assertEqual(expected, reordered)
        self.assertEqual(expected, reversed_order)
        self.assertEqual(
            expected.projection_fingerprint,
            reordered.projection_fingerprint,
        )
        self.assertEqual(expected.observation_count, 5)
        self.assertEqual(len(expected.entries), 3)

    def test_empty_input_produces_stable_empty_projection(self):
        from_empty_tuple = build_market_projection(())
        from_empty_iterator = build_market_projection(iter(()))

        expected_fingerprint = canonical_sha256(
            {
                "schema": PROJECTION_SCHEMA,
                "observation_count": 0,
                "entries": [],
            }
        )
        self.assertEqual(from_empty_tuple.entries, ())
        self.assertEqual(from_empty_tuple.observation_count, 0)
        self.assertEqual(
            from_empty_tuple.projection_fingerprint,
            expected_fingerprint,
        )
        self.assertEqual(from_empty_tuple, from_empty_iterator)

    def test_non_record_input_is_rejected(self):
        with self.assertRaises(TypeError):
            build_market_projection([object()])


class DecodePayloadTests(unittest.TestCase):
    def test_decode_payload_round_trips_projection_entries(self):
        payload = json.dumps(
            {"b": [1, 2], "a": {"x": True}, "имя": "Анкара"},
            ensure_ascii=False,
            sort_keys=True,
        )
        projection = build_market_projection([_record(payload=payload)])

        self.assertEqual(
            decode_payload(projection.entries[0]),
            {"b": [1, 2], "a": {"x": True}, "имя": "Анкара"},
        )

    def test_decode_payload_rejects_invalid_json(self):
        entry = _entry(payload_json="{not-json")

        with self.assertRaises(MarketContractError):
            decode_payload(entry)
        with self.assertRaises(TypeError):
            decode_payload(object())


if __name__ == "__main__":
    unittest.main()
