import unittest

from tools.bizman_collector.correlation import ActionHttpCorrelator
from tools.bizman_collector.events import EventSequencer


SESSION = "01991c7d-a400-7000-8000-000000000001"


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def monotonic(self) -> float:
        return self.value

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


def action(
    event_id: str,
    *,
    t: float,
    sequence: int = 0,
    target: str = "t1",
    frame: str | None = "f1",
    kind: str = "submit",
    trusted: bool = True,
    path: str | None = "/api/post",
    method: str | None = "POST",
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": SESSION,
        "sequence": sequence,
        "observed_at": "2026-09-07T00:00:00Z",
        "monotonic_time": t,
        "source": "dom.action",
        "event_type": "dom.action",
        "confidence": "observed",
        "target_id": target,
        "frame_id": frame,
        "action_kind": kind,
        "is_trusted": trusted,
        "form_action_path": path,
        "form_method": method,
    }


def request(
    event_id: str,
    *,
    t: float,
    sequence: int = 1,
    target: str = "t1",
    frame: str | None = "f1",
    path: str = "/api/post",
    method: str = "POST",
    user_gesture: bool = True,
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": SESSION,
        "sequence": sequence,
        "observed_at": "2026-09-07T00:00:00Z",
        "monotonic_time": t,
        "source": "cdp.network",
        "event_type": "http.request",
        "confidence": "observed",
        "target_id": target,
        "frame_id": frame,
        "url_path": path,
        "method": method,
        "has_user_gesture": user_gesture,
    }


class CorrelationTests(unittest.TestCase):
    def _correlator(self) -> ActionHttpCorrelator:
        return ActionHttpCorrelator(
            session_id=SESSION,
            sequencer=EventSequencer(),
            clock=FakeClock(),
        )

    def test_matching_submit_emits_strong_link(self):
        correlator = self._correlator()
        self.assertEqual(correlator.observe(action("a1", t=10.0, sequence=0)), [])
        links = correlator.observe(request("r1", t=10.1, sequence=1))
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["action_event_id"], "a1")
        self.assertEqual(link["network_event_id"], "r1")
        self.assertEqual(link["correlation_status"], "strong")
        self.assertNotEqual(link["correlation_status"], "exact")
        self.assertGreaterEqual(link["correlation_score"], 0.8)
        self.assertAlmostEqual(link["delta_ms"], 100.0)
        self.assertIn("same-frame", link["signals"])
        self.assertIn("form-path-match", link["signals"])
        self.assertIn("method-match", link["signals"])

    def test_different_target_or_conflicting_frame_never_correlates(self):
        correlator = self._correlator()
        correlator.observe(action("a1", t=10.0, sequence=0, target="t1", frame="f1"))
        self.assertEqual(
            correlator.observe(
                request("r1", t=10.05, sequence=1, target="t2", frame="f1")
            ),
            [],
        )
        self.assertEqual(
            correlator.observe(
                request("r2", t=10.05, sequence=2, target="t1", frame="f2")
            ),
            [],
        )

    def test_temporal_only_is_possible_but_never_exact(self):
        correlator = self._correlator()
        correlator.observe(
            action(
                "a1",
                t=20.0,
                sequence=0,
                kind="click",
                trusted=True,
                path=None,
                method=None,
            )
        )
        links = correlator.observe(
            request(
                "r1",
                t=20.1,
                sequence=1,
                path="/unrelated",
                method="GET",
                user_gesture=False,
            )
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["correlation_status"], "temporal-only")

    def test_stale_action_is_ignored(self):
        correlator = self._correlator()
        correlator.observe(action("a1", t=10.0, sequence=0))
        self.assertEqual(correlator.observe(request("r1", t=12.5, sequence=1)), [])

    def test_one_action_can_link_multiple_requests_but_request_only_once(self):
        correlator = self._correlator()
        correlator.observe(action("a1", t=30.0, sequence=0))
        first = correlator.observe(request("r1", t=30.05, sequence=1))
        second = correlator.observe(request("r2", t=30.08, sequence=3))
        duplicate = correlator.observe(request("r1", t=30.05, sequence=1))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(duplicate, [])
        self.assertEqual(first[0]["action_event_id"], second[0]["action_event_id"])

    def test_later_action_can_match_recent_unlinked_request(self):
        correlator = self._correlator()
        self.assertEqual(correlator.observe(request("r1", t=40.1, sequence=0)), [])
        links = correlator.observe(action("a1", t=40.0, sequence=1))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["network_event_id"], "r1")

    def test_best_action_wins_for_one_request(self):
        correlator = self._correlator()
        correlator.observe(
            action(
                "weak",
                t=50.0,
                sequence=0,
                kind="click",
                path=None,
                method=None,
            )
        )
        correlator.observe(action("strong", t=50.09, sequence=1))
        links = correlator.observe(request("r1", t=50.1, sequence=2))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["action_event_id"], "strong")


if __name__ == "__main__":
    unittest.main()
