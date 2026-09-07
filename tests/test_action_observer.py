import json
import unittest

from tools.bizman_collector.action_script import build_action_observer_script
from tools.bizman_collector.actions import ActionNormalizer, ExecutionContextRegistry
from tools.bizman_collector.events import EventSequencer
from tools.bizman_foundation.redaction import RedactionPolicy


class FakeClock:
    def __init__(self, monotonic_value: float = 50.0):
        self.monotonic_value = monotonic_value

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


class ActionScriptTests(unittest.TestCase):
    def test_script_never_reads_sensitive_value_or_storage_surfaces(self):
        script = build_action_observer_script(
            "__bizmanObserveV1",
            ("bizmania.ru",),
        )
        forbidden = (
            ".value",
            "innerHTML",
            "outerHTML",
            "document.cookie",
            "localStorage",
            "sessionStorage",
            "clipboard",
        )
        for token in forbidden:
            self.assertNotIn(token, script)
        self.assertIn("addEventListener", script)
        self.assertIn("click", script)
        self.assertIn("change", script)
        self.assertIn("submit", script)
        self.assertIn("location.pathname", script)


class ExecutionContextRegistryTests(unittest.TestCase):
    def test_context_lifecycle_maps_binding_context_to_frame(self):
        registry = ExecutionContextRegistry()
        registry.register(
            session_id="session-1",
            context_id=7,
            frame_id="frame-1",
            world_name="bizman-observer-v1",
            origin="https://bizmania.ru",
        )
        self.assertEqual(registry.frame_for("session-1", 7), "frame-1")
        self.assertEqual(registry.origin_for("session-1", 7), "https://bizmania.ru")
        registry.remove_context("session-1", 7)
        self.assertIsNone(registry.frame_for("session-1", 7))
        self.assertIsNone(registry.origin_for("session-1", 7))

        registry.register(
            session_id="session-1",
            context_id=8,
            frame_id="frame-2",
            world_name="bizman-observer-v1",
        )
        registry.clear_session("session-1")
        self.assertIsNone(registry.frame_for("session-1", 8))


class ActionNormalizerTests(unittest.TestCase):
    def _normalizer(self) -> ActionNormalizer:
        return ActionNormalizer(
            session_id="01991c7d-a400-7000-8000-000000000001",
            redaction=RedactionPolicy.default(),
            sequencer=EventSequencer(),
            clock=FakeClock(),
        )

    def test_submit_payload_is_bounded_redacted_and_contains_no_values(self):
        payload = {
            "schema": 1,
            "kind": "submit",
            "wallTimeMs": 1788750000500,
            "performanceTimeMs": 1234.5,
            "isTrusted": True,
            "pagePath": "/units/vendor/?accessToken=never-store",
            "element": {
                "tag": "form",
                "type": "",
                "name": "vendorForm",
                "role": "form",
                "selector": "form#vendor-form",
                "value": "TOP_SECRET_ELEMENT_VALUE",
                "text": "TOP_SECRET_TEXT",
                "innerHTML": "TOP_SECRET_HTML",
            },
            "form": {
                "actionPath": "/api/post?token=never-store",
                "method": "post",
                "fieldNames": ["safe", "quantity", "password", "clientSecret", "accessToken"],
                "values": {"safe": "TOP_SECRET_FORM_VALUE"},
            },
            "cookie": "TOP_SECRET_COOKIE",
        }
        event = self._normalizer().normalize_binding(
            json.dumps(payload),
            target_id="target-1",
            frame_id="frame-1",
        )
        assert event is not None
        self.assertEqual(event["source"], "dom.action")
        self.assertEqual(event["event_type"], "dom.action")
        self.assertEqual(event["action_kind"], "submit")
        self.assertEqual(event["page_path"], "/units/vendor/")
        self.assertEqual(event["form_action_path"], "/api/post")
        self.assertEqual(event["form_method"], "POST")
        self.assertEqual(event["form_field_names"], ["safe", "quantity"])
        self.assertEqual(event["element_tag"], "form")
        self.assertEqual(event["safe_selector"], "form#vendor-form")
        self.assertEqual(event["source_monotonic_time"], 1.2345)
        self.assertEqual(event["monotonic_time"], 50.0)

        serialized = json.dumps(event, sort_keys=True)
        for secret in (
            "TOP_SECRET_ELEMENT_VALUE",
            "TOP_SECRET_TEXT",
            "TOP_SECRET_HTML",
            "TOP_SECRET_FORM_VALUE",
            "TOP_SECRET_COOKIE",
            "never-store",
            "password",
            "clientSecret",
            "accessToken",
        ):
            self.assertNotIn(secret, serialized)

    def test_malformed_oversized_and_unknown_actions_are_dropped(self):
        normalizer = self._normalizer()
        self.assertIsNone(
            normalizer.normalize_binding("not-json", target_id="t", frame_id="f")
        )
        self.assertIsNone(
            normalizer.normalize_binding(
                json.dumps({"schema": 1, "kind": "keydown"}),
                target_id="t",
                frame_id="f",
            )
        )
        self.assertIsNone(
            normalizer.normalize_binding(
                json.dumps({"schema": 1, "kind": "click", "padding": "x" * 20000}),
                target_id="t",
                frame_id="f",
            )
        )

    def test_sensitive_element_name_is_not_persisted(self):
        event = self._normalizer().normalize_binding(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "change",
                    "wallTimeMs": 1788750000500,
                    "performanceTimeMs": 50,
                    "isTrusted": True,
                    "pagePath": "/settings",
                    "element": {
                        "tag": "input",
                        "type": "password",
                        "name": "password",
                        "role": "",
                        "selector": "input#password",
                    },
                }
            ),
            target_id="t",
            frame_id="f",
        )
        assert event is not None
        self.assertIsNone(event["element_name"])
        self.assertIsNone(event["safe_selector"])


if __name__ == "__main__":
    unittest.main()
