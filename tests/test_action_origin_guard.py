import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_collector.action_script import build_action_observer_script
from tools.bizman_collector.actions import ActionNormalizer, ExecutionContextRegistry
from tools.bizman_collector.cdp import CdpEvent
from tools.bizman_collector.correlation import ActionHttpCorrelator
from tools.bizman_collector.discovery import ProtocolCapabilities
from tools.bizman_collector.events import EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.runtime import CollectorEventPipeline
from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_collector.targets import TargetOrchestrator
from tools.bizman_foundation.redaction import RedactionPolicy

SESSION = "01991c7d-a400-7000-8000-000000000001"
BINDING = "__bizmanActionV1"
WORLD = "bizman-action-observer-v1"


class FakeClock:
    def monotonic(self) -> float:
        return 100.0

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


class FakeWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append_event(self, event: dict) -> None:
        self.events.append(event)


class FakeCdp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []

    async def command(self, method: str, params=None, *, session_id=None):
        self.calls.append((method, params or {}, session_id))
        return {}


def capabilities() -> ProtocolCapabilities:
    return ProtocolCapabilities(
        commands=frozenset(
            {
                "Target.setAutoAttach",
                "Network.enable",
                "Runtime.enable",
                "Runtime.addBinding",
                "Page.addScriptToEvaluateOnNewDocument",
            }
        ),
        events=frozenset(
            {
                "Runtime.executionContextCreated",
                "Runtime.executionContextDestroyed",
                "Runtime.executionContextsCleared",
                "Runtime.bindingCalled",
            }
        ),
        command_parameters={
            "Runtime.addBinding": frozenset({"name", "executionContextName"}),
            "Page.addScriptToEvaluateOnNewDocument": frozenset(
                {"source", "worldName", "runImmediately"}
            ),
            "Network.enable": frozenset({"maxPostDataSize"}),
        },
    )


class ActionOriginGuardTests(unittest.TestCase):
    def test_browser_observer_is_gated_to_first_party_hosts(self):
        script = build_action_observer_script(BINDING, ("bizmania.ru", "127.0.0.1"))
        self.assertIn("location.hostname", script)
        self.assertIn("bizmania.ru", script)
        self.assertIn("127.0.0.1", script)
        self.assertIn("endsWith", script)

    def test_pipeline_rejects_binding_from_third_party_execution_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sequencer = EventSequencer()
            clock = FakeClock()
            first_party = FirstPartyPolicy(("bizmania.ru",))
            redaction = RedactionPolicy.default()
            contexts = ExecutionContextRegistry()
            writer = FakeWriter()
            pipeline = CollectorEventPipeline(
                binding_name=BINDING,
                observer_world_name=WORLD,
                first_party=first_party,
                contexts=contexts,
                action_normalizer=ActionNormalizer(
                    session_id=SESSION,
                    redaction=redaction,
                    sequencer=sequencer,
                    clock=clock,
                ),
                network_normalizer=NetworkNormalizer(
                    session_id=SESSION,
                    first_party=first_party,
                    redaction=redaction,
                    artifacts=ArtifactStore(root),
                    sequencer=sequencer,
                    clock=clock,
                ),
                correlator=ActionHttpCorrelator(
                    session_id=SESSION,
                    sequencer=sequencer,
                    clock=clock,
                ),
                writer=writer,
            )
            pipeline.handle_runtime(
                CdpEvent(
                    method="Runtime.executionContextCreated",
                    params={
                        "context": {
                            "id": 7,
                            "name": WORLD,
                            "origin": "https://evil.example",
                            "auxData": {"frameId": "f1"},
                        }
                    },
                    session_id="cdp-session-1",
                ),
                target_id="t1",
            )
            self.assertEqual(
                contexts.origin_for("cdp-session-1", 7),
                "https://evil.example",
            )
            payload = json.dumps(
                {
                    "schema": 1,
                    "kind": "submit",
                    "wallTimeMs": 1788750000000,
                    "performanceTimeMs": 100000,
                    "isTrusted": True,
                    "pagePath": "/private-third-party-page",
                    "element": {"tag": "button", "type": "submit"},
                    "form": {
                        "actionPath": "/steal",
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
            self.assertEqual(writer.events, [])


class CrossOriginTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_origin_iframe_is_not_instrumented(self):
        cdp = FakeCdp()
        orchestrator = TargetOrchestrator(
            cdp=cdp,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            capabilities=capabilities(),
            action_binding_name=BINDING,
            action_world_name=WORLD,
            action_script="(() => {})();",
        )
        await orchestrator.handle_event(
            CdpEvent(
                method="Target.attachedToTarget",
                params={
                    "sessionId": "third-party-session",
                    "targetInfo": {
                        "targetId": "third-party-frame",
                        "type": "iframe",
                        "url": "https://evil.example/frame",
                    },
                },
                session_id="parent-session",
            )
        )
        third_party_methods = [
            method
            for method, _params, session_id in cdp.calls
            if session_id == "third-party-session"
        ]
        self.assertEqual(third_party_methods, [])

        await orchestrator.handle_event(
            CdpEvent(
                method="Target.attachedToTarget",
                params={
                    "sessionId": "first-party-session",
                    "targetInfo": {
                        "targetId": "first-party-frame",
                        "type": "iframe",
                        "url": "https://bizmania.ru/frame",
                    },
                },
                session_id="parent-session",
            )
        )
        first_party_methods = [
            method
            for method, _params, session_id in cdp.calls
            if session_id == "first-party-session"
        ]
        self.assertIn("Network.enable", first_party_methods)
        self.assertIn("Runtime.addBinding", first_party_methods)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", first_party_methods)


if __name__ == "__main__":
    unittest.main()
