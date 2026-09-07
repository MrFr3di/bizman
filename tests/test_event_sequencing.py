import tempfile
import unittest
from pathlib import Path

from tools.bizman_collector.events import EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_foundation.redaction import RedactionPolicy


class FakeClock:
    def __init__(self, values: list[float]):
        self._values = iter(values)

    def monotonic(self) -> float:
        return next(self._values)

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


class EventSequencerTests(unittest.TestCase):
    def test_shared_sequencer_is_contiguous(self):
        sequencer = EventSequencer()
        self.assertEqual([sequencer.next(), sequencer.next(), sequencer.next()], [0, 1, 2])

    def test_network_normalizers_share_global_sequence_and_collector_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            sequencer = EventSequencer()
            clock = FakeClock([100.0, 101.0])
            kwargs = {
                "session_id": "01991c7d-a400-7000-8000-000000000001",
                "first_party": FirstPartyPolicy(("bizmania.ru",)),
                "redaction": RedactionPolicy.default(),
                "artifacts": ArtifactStore(Path(tmp)),
                "sequencer": sequencer,
                "clock": clock,
            }
            first = NetworkNormalizer(**kwargs)
            second = NetworkNormalizer(**kwargs)

            event_a = first.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "a",
                    "timestamp": 12.5,
                    "wallTime": 1788750000.0,
                    "request": {
                        "url": "https://bizmania.ru/a",
                        "method": "GET",
                        "headers": {},
                    },
                },
                target_id="target-1",
            )
            event_b = second.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "b",
                    "timestamp": 13.5,
                    "wallTime": 1788750001.0,
                    "request": {
                        "url": "https://bizmania.ru/b",
                        "method": "GET",
                        "headers": {},
                    },
                },
                target_id="target-1",
            )

            assert event_a is not None and event_b is not None
            self.assertEqual((event_a["sequence"], event_b["sequence"]), (0, 1))
            self.assertEqual((event_a["monotonic_time"], event_b["monotonic_time"]), (100.0, 101.0))
            self.assertEqual(
                (event_a["source_monotonic_time"], event_b["source_monotonic_time"]),
                (12.5, 13.5),
            )


if __name__ == "__main__":
    unittest.main()
