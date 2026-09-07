import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_collector.actions import ActionNormalizer, ExecutionContextRegistry
from tools.bizman_collector.cdp import CdpEvent
from tools.bizman_collector.correlation import ActionHttpCorrelator
from tools.bizman_collector.events import EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.runtime import CollectorEventPipeline
from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_foundation.redaction import RedactionPolicy


SESSION = "01991c7d-a400-7000-8000-000000000001"
BINDING = "__bizmanActionV1"
WORLD = "bizman-action-observer-v1"


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def monotonic(self) -> float:
        return self.value

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


class FakeWriter:
    def __init__(self):
        self.events: list[dict] = []

    def append_event(self, event: dict) -> None:
        self.events.append(event)


class CollectorEventPipelineTests(unittest.TestCase):
    def _pipeline(self, root: Path):
        sequencer = EventSequencer()
        clock = FakeClock()
        first_party = FirstPartyPolicy(("bizmania.ru", "127.0.0.1"))
        redaction = RedactionPolicy.default()
        writer = FakeWriter()
        contexts = ExecutionContextRegistry()
        action_normalizer = ActionNormalizer(
            session_id=SESSION,
            redaction=redaction,
            sequencer=sequencer,
            clock=clock,
        )
        network_normalizer = NetworkNormalizer(
            session_id=SESSION,
            first_party=first_party,
            redaction=redaction,
            artifacts=ArtifactStore(root),
            sequencer=sequencer,
            clock=clock,
        )
        correlator = ActionHttpCorrelator(
            session_id=SESSION,
            sequencer=sequencer,
            clock=clock,
        )
        pipeline = CollectorEventPipeline(
            binding_name=BINDING,
            first_party=first_party,
            contexts=contexts,
            action_normalizer=action_normalizer,
            network_normalizer=network_normalizer,
            correlator=correlator,
            writer=writer,
        )
        return pipeline, writer, contexts

    def test_runtime_binding_then_request_emits_action_request_and_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline, writer, _ = self._pipeline(Path(tmp))
            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.executionContextCreated",
                    params={
                        "context": {
                            "id": 7,
                            "name": WORLD,
                            "origin": "https://bizmania.ru",
                            "auxData": {"frameId": "f1"},
                        }
                    },
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            payload = json.dumps(
                {
                    "schema": 1,
                    "kind": "submit",
                    "wallTimeMs": 1788750000000,
                    "performanceTimeMs": 100000,
                    "isTrusted": True,
                    "pagePath": "/fixture",
                    "element": {
                        "tag": "button",
                        "type": "submit",
                        "name": "save",
                        "role": "button",
                        "selector": "button#save",
                    },
                    "form": {
                        "actionPath": "/api/post",
                        "method": "post",
                        "fieldNames": ["product"],
                    },
                }
            )
            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.bindingCalled",
                    params={
                        "name": BINDING,
                        "payload": payload,
                        "executionContextId": 7,
                    },
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            pipeline.handle_network(
                CdpEvent(
                    method="Network.requestWillBeSent",
                    params={
                        "requestId": "r1",
                        "frameId": "f1",
                        "timestamp": 10.0,
                        "wallTime": 1788750000.1,
                        "hasUserGesture": True,
                        "request": {
                            "url": "http://127.0.0.1/api/post",
                            "method": "POST",
                            "headers": {},
                        },
                    },
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )

            self.assertEqual(
                [event["event_type"] for event in writer.events],
                ["dom.action", "http.request", "correlation.action_http"],
            )
            self.assertEqual([event["sequence"] for event in writer.events], [0, 1, 2])
            action, request, link = writer.events
            self.assertEqual(action["frame_id"], "f1")
            self.assertEqual(link["action_event_id"], action["event_id"])
            self.assertEqual(link["network_event_id"], request["event_id"])
            self.assertEqual(link["correlation_status"], "strong")

    def test_unrelated_binding_is_ignored_and_context_lifecycle_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline, writer, contexts = self._pipeline(Path(tmp))
            created = CdpEvent(
                method="Runtime.executionContextCreated",
                params={
                    "context": {
                        "id": 9,
                        "name": WORLD,
                        "origin": "https://bizmania.ru",
                        "auxData": {"frameId": "f9"},
                    }
                },
                session_id="cdp-session-1",
            )
            pipeline.handle_runtime(created, target_id="t1")
            self.assertEqual(contexts.frame_for("cdp-session-1", 9), "f9")

            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.bindingCalled",
                    params={"name": "otherBinding", "payload": "{}", "executionContextId": 9},
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            self.assertEqual(writer.events, [])

            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.executionContextDestroyed",
                    params={"executionContextId": 9},
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            self.assertIsNone(contexts.frame_for("cdp-session-1", 9))

            contexts.register(
                session_id="cdp-session-1",
                context_id=10,
                frame_id="f10",
                world_name=WORLD,
            )
            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.executionContextsCleared",
                    params={},
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            self.assertIsNone(contexts.frame_for("cdp-session-1", 10))


if __name__ == "__main__":
    unittest.main()
