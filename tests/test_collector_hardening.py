import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tools.bizman_collector.cdp import (
    CdpCommandRejected,
    CdpConnection,
    CdpConnectionClosed,
    CdpEvent,
)
from tools.bizman_collector.discovery import BrowserDiscovery
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.runtime import run_collection
from tools.bizman_collector.storage import ArtifactStore
from tools.bizman_collector.targets import TargetOrchestrator
from tools.bizman_foundation.redaction import RedactionPolicy


class FakeTransport:
    def __init__(self):
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: asyncio.Queue[dict] = asyncio.Queue()

    async def send(self, message: str) -> None:
        await self.sent.put(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        await self.incoming.put(None)


class DiscoveryCapabilitiesTests(unittest.TestCase):
    def test_discovery_builds_capability_map_from_running_protocol(self):
        discovery = BrowserDiscovery.from_documents(
            endpoint="http://127.0.0.1:9222",
            version_document={
                "Browser": "Chrome/140.0.7339.12",
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
            },
            protocol_document={
                "version": {"major": "1", "minor": "3"},
                "domains": [
                    {
                        "domain": "Network",
                        "commands": [
                            {
                                "name": "enable",
                                "parameters": [{"name": "maxPostDataSize"}],
                            }
                        ],
                        "events": [{"name": "requestWillBeSent"}],
                    }
                ],
            },
        )
        self.assertTrue(discovery.capabilities.has_command("Network.enable"))
        self.assertTrue(
            discovery.capabilities.command_supports_parameter(
                "Network.enable", "maxPostDataSize"
            )
        )
        self.assertTrue(
            discovery.capabilities.has_event("Network.requestWillBeSent")
        )


class CdpSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_command_fails_when_transport_closes(self):
        transport = FakeTransport()
        connection = CdpConnection(transport)
        receiver = asyncio.create_task(connection.receive_loop())
        pending = asyncio.create_task(connection.command("Browser.getVersion"))
        await transport.sent.get()
        await transport.close()
        with self.assertRaises(CdpConnectionClosed):
            await pending
        await receiver

    async def test_passive_allowlist_blocks_mutating_commands_before_send(self):
        transport = FakeTransport()
        connection = CdpConnection(
            transport,
            allowed_methods=frozenset({"Network.enable"}),
        )
        with self.assertRaises(CdpCommandRejected):
            await connection.command(
                "Network.setCookie", {"name": "x", "value": "y"}
            )
        self.assertTrue(transport.sent.empty())


class NetworkPrivacyRegressionTests(unittest.TestCase):
    def _normalizer(self, root: Path) -> NetworkNormalizer:
        return NetworkNormalizer(
            session_id="01991c7d-a400-7000-8000-000000000001",
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            redaction=RedactionPolicy.default(),
            artifacts=ArtifactStore(root),
        )

    def test_redirect_to_third_party_evicts_first_party_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self.assertIsNotNone(
                normalizer.normalize(
                    method="Network.requestWillBeSent",
                    params={
                        "requestId": "r1",
                        "timestamp": 1.0,
                        "request": {
                            "url": "https://bizmania.ru/redirect",
                            "method": "GET",
                            "headers": {},
                        },
                    },
                    target_id="t1",
                )
            )
            self.assertIsNone(
                normalizer.normalize(
                    method="Network.requestWillBeSent",
                    params={
                        "requestId": "r1",
                        "timestamp": 1.1,
                        "redirectResponse": {
                            "url": "https://bizmania.ru/redirect",
                            "status": 302,
                        },
                        "request": {
                            "url": "https://example.com/landing",
                            "method": "GET",
                            "headers": {},
                        },
                    },
                    target_id="t1",
                )
            )
            self.assertIsNone(
                normalizer.normalize(
                    method="Network.loadingFinished",
                    params={
                        "requestId": "r1",
                        "timestamp": 1.2,
                        "encodedDataLength": 1,
                    },
                    target_id="t1",
                )
            )

    def test_websocket_frame_payload_is_never_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            created = normalizer.normalize(
                method="Network.webSocketCreated",
                params={
                    "requestId": "w1",
                    "timestamp": 1.0,
                    "url": "wss://bizmania.ru/socket?token=secret",
                },
                target_id="t1",
            )
            self.assertIsNotNone(created)
            self.assertEqual(created["query"], {})
            frame = normalizer.normalize(
                method="Network.webSocketFrameReceived",
                params={
                    "requestId": "w1",
                    "timestamp": 1.1,
                    "response": {
                        "opcode": 1,
                        "mask": False,
                        "payloadData": "SECRET_PAYLOAD",
                    },
                },
                target_id="t1",
            )
            self.assertIsNotNone(frame)
            self.assertNotIn("payloadData", frame)
            self.assertNotIn("SECRET_PAYLOAD", json.dumps(frame))


class FakeCdp:
    def __init__(self):
        self.calls: list[tuple[str, dict, str | None]] = []
        self.counter = 0

    async def command(self, method: str, params=None, *, session_id=None):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": []}
        if method == "Target.attachToTarget":
            self.counter += 1
            return {"sessionId": f"session-{self.counter}"}
        return {}


class TargetLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_page_and_recursive_child_attach_without_duplicates(self):
        cdp = FakeCdp()
        orchestrator = TargetOrchestrator(
            cdp=cdp,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
        )
        await orchestrator.bootstrap()
        event = CdpEvent(
            "Target.targetCreated",
            {
                "targetInfo": {
                    "targetId": "page-3",
                    "type": "page",
                    "url": "https://bizmania.ru/new",
                }
            },
            None,
        )
        await orchestrator.handle_event(event)
        await orchestrator.handle_event(
            CdpEvent("Target.targetInfoChanged", event.params, None)
        )
        attaches = [
            call
            for call in cdp.calls
            if call[0] == "Target.attachToTarget"
            and call[1].get("targetId") == "page-3"
        ]
        self.assertEqual(len(attaches), 1)

        parent_session = orchestrator.registry.target_to_session["page-3"]
        await orchestrator.handle_event(
            CdpEvent(
                "Target.attachedToTarget",
                {
                    "sessionId": "child-1",
                    "targetInfo": {
                        "targetId": "worker-1",
                        "type": "worker",
                        "url": "",
                    },
                },
                parent_session,
            )
        )
        self.assertEqual(
            orchestrator.registry.target_for_session("child-1"), "worker-1"
        )
        self.assertIn(("Network.enable", {}, "child-1"), cdp.calls)


class AutoRespondTransport:
    def __init__(self):
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        command = json.loads(message)
        result = {}
        if command["method"] == "Target.getTargets":
            result = {
                "targetInfos": [
                    {
                        "targetId": "page-1",
                        "type": "page",
                        "url": "https://bizmania.ru/company?id=1",
                        "title": "BizMania",
                    }
                ]
            }
        elif command["method"] == "Target.attachToTarget":
            result = {"sessionId": "session-1"}
        await self.incoming.put(
            json.dumps({"id": command["id"], "result": result})
        )
        if (
            command["method"] == "Target.setAutoAttach"
            and command.get("sessionId") == "session-1"
        ):
            await self.incoming.put(
                json.dumps(
                    {
                        "method": "Network.requestWillBeSent",
                        "sessionId": "session-1",
                        "params": {
                            "requestId": "r1",
                            "loaderId": "l1",
                            "frameId": "f1",
                            "timestamp": 2.0,
                            "request": {
                                "url": "https://bizmania.ru/company?id=1",
                                "method": "GET",
                                "headers": {},
                            },
                        },
                    }
                )
            )
            await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.incoming.put(None)


class FakeTransportContext:
    def __init__(self, transport):
        self.transport = transport

    async def __aenter__(self):
        return self.transport

    async def __aexit__(self, exc_type, exc, tb):
        await self.transport.close()


class RuntimeManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_manifest_is_draft_2020_12_valid(self):
        discovery = BrowserDiscovery.from_documents(
            endpoint="http://127.0.0.1:9222",
            version_document={
                "Browser": "Chrome/140.0.7339.12",
                "Protocol-Version": "1.3",
                "User-Agent": "ua",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
            },
            protocol_document={
                "version": {"major": "1", "minor": "3"}, "domains": []
            },
        )
        transport = AutoRespondTransport()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = await asyncio.wait_for(
                run_collection(
                    endpoint=discovery.endpoint,
                    data_dir=root,
                    hosts=("bizmania.ru",),
                    redaction_policy=RedactionPolicy.default(),
                    discovery=discovery,
                    transport_factory=lambda _uri: FakeTransportContext(transport),
                ),
                2,
            )
            manifest = json.loads(
                (root / "sessions" / session_id / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "schemas/session-manifest.schema.json"
                ).read_text(encoding="utf-8")
            )
            errors = list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(manifest)
            )
            self.assertEqual(errors, [])
            self.assertEqual(manifest["status"], "completed")
            self.assertRegex(
                manifest["protocol"]["artifact_ref"], r"^sha256:[0-9a-f]{64}$"
            )


if __name__ == "__main__":
    unittest.main()
