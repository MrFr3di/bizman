import json
import unittest

from tools.bizman_collector.actions import ActionNormalizer
from tools.bizman_collector.events import EventSequencer
from tools.bizman_foundation.redaction import RedactionPolicy


class FakeClock:
    def monotonic(self) -> float:
        return 1.0

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


class ActionPayloadBoundaryTests(unittest.TestCase):
    def test_spoofed_absolute_form_action_is_not_persisted(self):
        normalizer = ActionNormalizer(
            session_id="01991c7d-a400-7000-8000-000000000001",
            redaction=RedactionPolicy.default(),
            sequencer=EventSequencer(),
            clock=FakeClock(),
        )
        event = normalizer.normalize_binding(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "submit",
                    "pagePath": "/safe",
                    "form": {
                        "actionPath": "https://evil.example/steal?token=secret",
                        "method": "post",
                        "fieldNames": ["safe"],
                    },
                }
            ),
            target_id="t1",
            frame_id="f1",
        )
        assert event is not None
        self.assertIsNone(event["form_action_path"])
        self.assertNotIn("evil.example", json.dumps(event, sort_keys=True))
        self.assertNotIn("secret", json.dumps(event, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
