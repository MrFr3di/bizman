import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import tools.bizman_collector.runtime as runtime_module
import tools.ci.verify_collector_e2e as e2e_verifier
from tools.bizman_collector.cdp import CdpConnectionClosed, CdpEvent, CdpProtocolError
from tools.bizman_collector.correlation import ActionHttpCorrelator
from tools.bizman_collector.discovery import ProtocolCapabilities
from tools.bizman_collector.events import EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_collector.targets import TargetOrchestrator
from tools.bizman_foundation.redaction import RedactionPolicy, redact_headers


SESSION = "01991c7d-a400-7000-8000-000000000001"
BINDING = "__bizmanActionV1"
WORLD = "bizman-action-observer-v1"


class FakeClock:
    def monotonic(self) -> float:
        return 100.0

    def wall_iso(self) -> str:
        return "2026-09-07T00:00:00Z"


def action(event_id: str, *, sequence: int, t: float, path=None, method=None) -> dict:
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
        "target_id": "t1",
        "frame_id": "f1",
        "action_kind": "submit",
        "is_trusted": True,
        "form_action_path": path,
        "form_method": method,
    }


def request(event_id: str, *, sequence: int, t: float, path="/api/post", method="POST") -> dict:
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
        "target_id": "t1",
        "frame_id": "f1",
        "url_path": path,
        "method": method,
        "has_user_gesture": False,
    }


class CorrelatorDeepReviewTests(unittest.TestCase):
    def _correlator(self, *, max_recent: int = 8) -> ActionHttpCorrelator:
        return ActionHttpCorrelator(
            session_id=SESSION,
            sequencer=EventSequencer(),
            clock=FakeClock(),
            max_recent=max_recent,
        )

    def test_method_match_alone_never_promotes_to_probable(self):
        correlator = self._correlator()
        correlator.observe(
            action("a1", sequence=0, t=10.0, path=None, method="POST")
        )
        links = correlator.observe(
            request("r1", sequence=1, t=10.05, path="/unrelated", method="POST")
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["correlation_status"], "temporal-only")

    def test_internal_dedupe_state_stays_bounded_after_long_session(self):
        correlator = self._correlator(max_recent=2)
        sequence = 0
        for index in range(24):
            t = float(index * 3)
            correlator.observe(
                action(
                    f"a{index}",
                    sequence=sequence,
                    t=t,
                    path="/api/post",
                    method="POST",
                )
            )
            sequence += 1
            links = correlator.observe(
                request(f"r{index}", sequence=sequence, t=t + 0.05)
            )
            sequence += 1
            self.assertEqual(len(links), 1)

        set_sizes = [
            len(value)
            for value in vars(correlator).values()
            if isinstance(value, set)
        ]
        self.assertTrue(all(size <= correlator.max_recent for size in set_sizes))
        self.assertLessEqual(len(correlator._actions), correlator.max_recent)
        self.assertLessEqual(len(correlator._requests), correlator.max_recent)

    def test_replay_of_old_source_sequence_is_rejected(self):
        correlator = self._correlator(max_recent=1)
        first_action = action(
            "a1", sequence=0, t=10.0, path="/api/post", method="POST"
        )
        first_request = request("r1", sequence=1, t=10.05)
        correlator.observe(first_action)
        self.assertEqual(len(correlator.observe(first_request)), 1)
        correlator.observe(
            action("a2", sequence=2, t=10.10, path="/api/post", method="POST")
        )
        self.assertEqual(correlator.observe(dict(first_request)), [])


class RetryCdp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []
        self.binding_failures = 1

    async def command(self, method: str, params=None, *, session_id=None):
        self.calls.append((method, params or {}, session_id))
        if method == "Runtime.addBinding" and self.binding_failures:
            self.binding_failures -= 1
            raise CdpProtocolError(-32000, "transient binding failure")
        return {}


def action_capabilities() -> ProtocolCapabilities:
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


class TargetRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_binding_installation_is_retryable(self):
        cdp = RetryCdp()
        warnings: list[str] = []
        orchestrator = TargetOrchestrator(
            cdp=cdp,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            capabilities=action_capabilities(),
            action_binding_name=BINDING,
            action_world_name=WORLD,
            action_script="(() => {})();",
            warning_sink=warnings.append,
        )
        info = {
            "targetId": "frame-1",
            "type": "iframe",
            "url": "https://bizmania.ru/frame",
        }
        await orchestrator.handle_event(
            CdpEvent(
                method="Target.attachedToTarget",
                params={"sessionId": "session-1", "targetInfo": info},
                session_id="parent",
            )
        )
        await orchestrator.handle_event(
            CdpEvent(
                method="Target.targetInfoChanged",
                params={"targetInfo": info},
                session_id="parent",
            )
        )

        methods = [
            method
            for method, _params, session_id in cdp.calls
            if session_id == "session-1"
        ]
        self.assertEqual(methods.count("Network.enable"), 1)
        self.assertEqual(methods.count("Runtime.addBinding"), 2)
        self.assertEqual(methods.count("Page.addScriptToEvaluateOnNewDocument"), 1)
        self.assertTrue(any("transient binding failure" in item for item in warnings))


class EventSource:
    def __init__(self, event: CdpEvent) -> None:
        self.event = event
        self.closed = False
        self.sent = False

    async def next_event(self):
        if not self.sent:
            self.sent = True
            return self.event
        self.closed = True
        raise CdpConnectionClosed("done")


class NoopCdp:
    async def command(self, method: str, params=None, *, session_id=None):
        return {}


class PipelineSpy:
    def __init__(self) -> None:
        self.cleared: list[str | None] = []

    def clear_session(self, session_id):
        self.cleared.append(session_id)


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_target_terminal_event_clears_session(self, method: str) -> None:
        orchestrator = TargetOrchestrator(
            cdp=NoopCdp(),
            first_party=FirstPartyPolicy(("bizmania.ru",)),
        )
        orchestrator.registry.register(
            target_id="target-1",
            session_id="session-1",
            info={"targetId": "target-1", "type": "page", "url": "https://bizmania.ru/"},
        )
        pipeline = PipelineSpy()
        await runtime_module._consume_events(
            cdp=EventSource(
                CdpEvent(
                    method=method,
                    params={"targetId": "target-1"},
                    session_id=None,
                )
            ),
            orchestrator=orchestrator,
            pipeline=pipeline,
        )
        self.assertIn("session-1", pipeline.cleared)

    async def test_target_destroyed_clears_execution_context_session(self):
        await self._assert_target_terminal_event_clears_session("Target.targetDestroyed")

    async def test_target_crashed_clears_execution_context_session(self):
        await self._assert_target_terminal_event_clears_session("Target.targetCrashed")


class CapabilityReviewTests(unittest.TestCase):
    def test_empty_capabilities_explain_why_observer_is_disabled(self):
        issues = runtime_module._action_instrumentation_issues(ProtocolCapabilities())
        self.assertTrue(any("no capabilities" in item.casefold() for item in issues))

    def test_secure_action_support_requires_world_binding_parameters_and_events(self):
        incomplete = ProtocolCapabilities(
            commands=frozenset(
                {
                    "Runtime.enable",
                    "Runtime.addBinding",
                    "Page.addScriptToEvaluateOnNewDocument",
                }
            ),
            events=frozenset({"Runtime.bindingCalled"}),
            command_parameters={
                "Runtime.addBinding": frozenset({"name"}),
                "Page.addScriptToEvaluateOnNewDocument": frozenset({"source"}),
            },
        )
        issues = runtime_module._action_instrumentation_issues(incomplete)
        combined = " ".join(issues)
        self.assertIn("executionContextName", combined)
        self.assertIn("worldName", combined)
        self.assertIn("Runtime.executionContextCreated", combined)


class PersistencePrivacyTests(unittest.TestCase):
    def test_header_redaction_drops_url_bearing_and_secret_named_headers(self):
        safe = redact_headers(
            {
                "Content-Type": "application/json",
                "Referer": "https://bizmania.ru/?accessToken=TOP_SECRET",
                "Location": "/next?token=TOP_SECRET",
                "Sec-WebSocket-Protocol": "bearer.TOP_SECRET",
                "X-AccessToken": "TOP_SECRET",
            },
            RedactionPolicy.default(),
        )
        self.assertEqual(safe, {"Content-Type": "application/json"})

    def test_unstructured_top_level_json_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = NetworkNormalizer(
                session_id=SESSION,
                first_party=FirstPartyPolicy(("bizmania.ru",)),
                redaction=RedactionPolicy.default(),
                artifacts=ArtifactStore(Path(tmp)),
            )
            for index, body in enumerate(("TOP_SECRET", ["TOP_SECRET"])):
                event = normalizer.normalize(
                    method="Network.requestWillBeSent",
                    params={
                        "requestId": f"r{index}",
                        "timestamp": float(index + 1),
                        "request": {
                            "url": "https://bizmania.ru/api/post",
                            "method": "POST",
                            "headers": {"Content-Type": "application/json"},
                            "postData": json.dumps(body),
                        },
                    },
                    target_id="t1",
                )
                assert event is not None
                self.assertIsNone(event.get("request_body_ref"))


class CasScannerTests(unittest.TestCase):
    def test_binary_artifact_is_scanned_for_synthetic_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifacts" / "sha256" / "aa" / ("a" * 64)
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"\xff\x00TOP_SECRET_BINARY\x80")
            with self.assertRaises(AssertionError):
                e2e_verifier._assert_no_artifact_secrets(
                    root,
                    ("TOP_SECRET_BINARY",),
                )


if __name__ == "__main__":
    unittest.main()
