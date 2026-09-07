import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tools.bizman_collector.cdp import CdpConnection, CdpProtocolError
from tools.bizman_collector.discovery import BrowserDiscovery
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.runtime import run_collection
from tools.bizman_collector.storage import ArtifactStore, SessionWriter
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


class DiscoveryAndStorageTests(unittest.TestCase):
    def test_discovery_records_exact_running_protocol(self):
        discovery = BrowserDiscovery.from_documents(
            endpoint="http://127.0.0.1:9222",
            version_document={
                "Browser": "Chrome/140.0.7339.12",
                "Protocol-Version": "1.3",
                "User-Agent": "ua",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
            },
            protocol_document={"version": {"major": "1", "minor": "3"}, "domains": []},
        )
        self.assertEqual(discovery.browser_product, "Chrome")
        self.assertEqual(discovery.browser_version, "140.0.7339.12")
        self.assertEqual(discovery.protocol_version, "1.3")
        self.assertRegex(discovery.protocol_sha256, r"^[0-9a-f]{64}$")

    def test_artifact_store_deduplicates_by_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            first = store.put_bytes(b"hello")
            second = store.put_bytes(b"hello")
            self.assertEqual(first, second)
            self.assertEqual(store.count, 1)
            self.assertEqual(store.read_bytes(first), b"hello")


class CdpConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_response_and_protocol_error(self):
        transport = FakeTransport()
        connection = CdpConnection(transport)
        receiver = asyncio.create_task(connection.receive_loop())

        command = asyncio.create_task(connection.command("Browser.getVersion"))
        sent = await transport.sent.get()
        await transport.incoming.put(json.dumps({"id": sent["id"], "result": {"ok": True}}))
        self.assertEqual(await command, {"ok": True})

        failing = asyncio.create_task(connection.command("Missing.method"))
        sent = await transport.sent.get()
        await transport.incoming.put(
            json.dumps({"id": sent["id"], "error": {"code": -32601, "message": "missing"}})
        )
        with self.assertRaises(CdpProtocolError):
            await failing

        await transport.close()
        await receiver

    async def test_event_preserves_flattened_session_id(self):
        transport = FakeTransport()
        connection = CdpConnection(transport)
        receiver = asyncio.create_task(connection.receive_loop())
        await transport.incoming.put(
            json.dumps({
                "method": "Network.loadingFinished",
                "params": {"requestId": "r1", "timestamp": 12.5},
                "sessionId": "s1",
            })
        )
        event = await asyncio.wait_for(connection.next_event(), 1)
        self.assertEqual(event.method, "Network.loadingFinished")
        self.assertEqual(event.session_id, "s1")
        await transport.close()
        await receiver


class NetworkNormalizerTests(unittest.TestCase):
    def _normalizer(self, root: Path) -> NetworkNormalizer:
        return NetworkNormalizer(
            session_id="01991c7d-a400-7000-8000-000000000001",
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            redaction=RedactionPolicy.default(),
            artifacts=ArtifactStore(root),
        )

    def test_request_is_first_party_redacted_and_schema_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            event = normalizer.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "r1",
                    "loaderId": "l1",
                    "frameId": "f1",
                    "timestamp": 12.5,
                    "wallTime": 1788750000.5,
                    "hasUserGesture": True,
                    "initiator": {"type": "script"},
                    "documentURL": "https://bizmania.ru/units/vendor/",
                    "request": {
                        "url": "https://bizmania.ru/units/vendor/select/?id=13548&accessToken=secret",
                        "method": "POST",
                        "headers": {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer secret",
                            "Cookie": "sid=secret",
                        },
                        "postData": json.dumps({"product": 1, "clientSecret": "x", "safe": "y"}),
                    },
                },
                target_id="t1",
            )
            assert event is not None
            self.assertEqual(event["query"], {"id": ["13548"]})
            self.assertNotIn("Authorization", event["headers"])
            self.assertEqual(event["initiator_type"], "script")
            body = normalizer.artifacts.read_bytes(event["request_body_ref"])
            self.assertEqual(json.loads(body), {"product": 1, "safe": "y"})

            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "schemas/event.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            errors = list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event)
            )
            self.assertEqual(errors, [])

    def test_redirects_increment_index_and_third_party_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            third_party = normalizer.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "x",
                    "timestamp": 0.5,
                    "request": {"url": "https://mc.yandex.ru/watch/1", "method": "GET", "headers": {}},
                },
                target_id="t1",
            )
            self.assertIsNone(third_party)

            first = normalizer.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "r1",
                    "timestamp": 1.0,
                    "request": {"url": "https://bizmania.ru/old", "method": "GET", "headers": {}},
                },
                target_id="t1",
            )
            second = normalizer.normalize(
                method="Network.requestWillBeSent",
                params={
                    "requestId": "r1",
                    "timestamp": 1.1,
                    "redirectResponse": {"url": "https://bizmania.ru/old", "status": 302},
                    "request": {"url": "https://bizmania.ru/new", "method": "GET", "headers": {}},
                },
                target_id="t1",
            )
            assert first is not None and second is not None
            self.assertEqual(first["redirect_index"], 0)
            self.assertEqual(second["redirect_index"], 1)
            self.assertEqual(second["redirect_from_path"], "/old")
            self.assertEqual(second["redirect_status_code"], 302)


class FakeCdp:
    def __init__(self):
        self.calls: list[tuple[str, dict, str | None]] = []

    async def command(self, method: str, params=None, *, session_id=None):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": "page-1", "type": "page", "url": "https://bizmania.ru/company?id=1", "title": "BizMania"},
                {"targetId": "page-2", "type": "page", "url": "https://example.com/", "title": "Other"},
            ]}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}


class TargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_uses_flattened_first_party_sessions(self):
        cdp = FakeCdp()
        orchestrator = TargetOrchestrator(cdp=cdp, first_party=FirstPartyPolicy(("bizmania.ru",)))
        await orchestrator.bootstrap()
        attach = [call for call in cdp.calls if call[0] == "Target.attachToTarget"]
        self.assertEqual(attach, [("Target.attachToTarget", {"targetId": "page-1", "flatten": True}, None)])
        self.assertIn(("Network.enable", {}, "session-1"), cdp.calls)
        self.assertIn(
            (
                "Target.setAutoAttach",
                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                "session-1",
            ),
            cdp.calls,
        )


class AutoRespondTransport:
    def __init__(self):
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        command = json.loads(message)
        result = {}
        if command["method"] == "Target.getTargets":
            result = {"targetInfos": [{"targetId": "page-1", "type": "page", "url": "https://bizmania.ru/company?id=1", "title": "BizMania"}]}
        elif command["method"] == "Target.attachToTarget":
            result = {"sessionId": "session-1"}
        await self.incoming.put(json.dumps({"id": command["id"], "result": result}))
        if command["method"] == "Target.setAutoAttach" and command.get("sessionId") == "session-1":
            await self.incoming.put(json.dumps({
                "method": "Network.requestWillBeSent",
                "sessionId": "session-1",
                "params": {
                    "requestId": "r1",
                    "loaderId": "l1",
                    "frameId": "f1",
                    "timestamp": 2.0,
                    "request": {"url": "https://bizmania.ru/company?id=1", "method": "GET", "headers": {}},
                },
            }))
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


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_persists_protocol_artifact_and_event(self):
        discovery = BrowserDiscovery.from_documents(
            endpoint="http://127.0.0.1:9222",
            version_document={
                "Browser": "Chrome/140.0.7339.12",
                "Protocol-Version": "1.3",
                "User-Agent": "ua",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
            },
            protocol_document={"version": {"major": "1", "minor": "3"}, "domains": []},
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
                (root / "sessions" / session_id / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIsNotNone(manifest["ended_at"])
            self.assertEqual(manifest["protocol"]["sha256"], discovery.protocol_sha256)
            self.assertRegex(manifest["protocol"]["artifact_ref"], r"^sha256:[0-9a-f]{64}$")
            event_path = root / manifest["event_files"][0]
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event_type"] for event in events], ["http.request"])


if __name__ == "__main__":
    unittest.main()
