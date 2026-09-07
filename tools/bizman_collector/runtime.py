from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from tools.bizman_collector.cdp import (
    CdpConnection,
    CdpConnectionClosed,
    CdpTransport,
    open_cdp_transport,
)
from tools.bizman_collector.discovery import BrowserDiscovery, discover_browser
from tools.bizman_collector.events import CollectorClock, EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.storage import ArtifactStore, SessionWriter
from tools.bizman_collector.targets import TargetOrchestrator
from tools.bizman_foundation.redaction import RedactionPolicy
from tools.bizman_foundation.session import new_session_manifest

COLLECTOR_VERSION = "0.1.0"

PASSIVE_CDP_METHODS = frozenset(
    {
        "Target.setDiscoverTargets",
        "Target.getTargets",
        "Target.attachToTarget",
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.enable",
        "Runtime.addBinding",
        "Page.addScriptToEvaluateOnNewDocument",
    }
)

TransportFactory = Callable[[str], AbstractAsyncContextManager[CdpTransport]]


async def _discover(endpoint: str) -> BrowserDiscovery:
    return await asyncio.to_thread(discover_browser, endpoint)


async def _consume_events(
    *,
    cdp: CdpConnection,
    orchestrator: TargetOrchestrator,
    normalizer: NetworkNormalizer,
    writer: SessionWriter,
) -> None:
    """Drain CDP events until a normally closed transport has no events left."""

    while True:
        timeout = 0.01 if cdp.closed else 0.25
        try:
            event = await asyncio.wait_for(cdp.next_event(), timeout=timeout)
        except TimeoutError:
            if cdp.closed:
                return
            continue
        except CdpConnectionClosed:
            return

        if event.method.startswith("Target."):
            await orchestrator.handle_event(event)

        if event.method.startswith("Network."):
            target_id = orchestrator.registry.target_for_session(event.session_id)
            normalized = normalizer.normalize(
                method=event.method,
                params=event.params,
                target_id=target_id,
            )
            if normalized is not None:
                writer.append_event(normalized)


async def run_collection(
    *,
    endpoint: str = "http://127.0.0.1:9222",
    data_dir: Path,
    hosts: tuple[str, ...] = ("bizmania.ru",),
    redaction_policy: RedactionPolicy | None = None,
    discovery: BrowserDiscovery | None = None,
    transport_factory: TransportFactory | None = None,
    event_queue_size: int = 8192,
) -> str:
    """Run one passive Chrome/CDP collection session.

    The function never launches or mutates Chrome. It only connects to an
    already-running DevTools endpoint and persists sanitized first-party
    observations under ``data_dir``.
    """

    if event_queue_size <= 0:
        raise ValueError("event_queue_size must be positive")

    resolved_discovery = discovery or await _discover(endpoint)
    redaction = redaction_policy or RedactionPolicy.default()
    first_party = FirstPartyPolicy(hosts)
    data_dir = Path(data_dir).expanduser()

    artifacts = ArtifactStore(data_dir / "artifacts")
    manifest = new_session_manifest(
        collector_version=COLLECTOR_VERSION,
        browser_product=resolved_discovery.browser_product,
        browser_version=resolved_discovery.browser_version,
        protocol_version=resolved_discovery.protocol_version,
    )
    manifest.setdefault("status", "running")
    manifest.setdefault("warnings", [])
    protocol = manifest.setdefault("protocol", {})
    protocol.setdefault("sha256", None)
    protocol.setdefault("artifact_ref", None)

    writer = SessionWriter(data_dir, manifest)
    protocol_ref = artifacts.put_bytes(resolved_discovery.protocol_bytes)
    writer.update_protocol_artifact(
        sha256=resolved_discovery.protocol_sha256,
        artifact_ref=protocol_ref,
    )

    sequencer = EventSequencer()
    clock = CollectorClock()
    normalizer = NetworkNormalizer(
        session_id=writer.session_id,
        first_party=first_party,
        redaction=redaction,
        artifacts=artifacts,
        sequencer=sequencer,
        clock=clock,
    )

    factory = transport_factory or open_cdp_transport
    status = "failed"
    try:
        async with factory(resolved_discovery.websocket_url) as transport:
            cdp = CdpConnection(
                transport,
                allowed_methods=PASSIVE_CDP_METHODS,
                event_queue_size=event_queue_size,
            )
            orchestrator = TargetOrchestrator(
                cdp=cdp,
                first_party=first_party,
                capabilities=resolved_discovery.capabilities,
                max_post_data_size=redaction.max_request_bytes,
                warning_sink=writer.add_warning,
            )

            async with asyncio.TaskGroup() as group:
                group.create_task(cdp.receive_loop(), name="bizman-cdp-receiver")
                await orchestrator.bootstrap()
                group.create_task(
                    _consume_events(
                        cdp=cdp,
                        orchestrator=orchestrator,
                        normalizer=normalizer,
                        writer=writer,
                    ),
                    name="bizman-cdp-consumer",
                )
        status = "completed"
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except BaseException as exc:
        writer.add_warning(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        writer.finalize(status=status)

    return writer.session_id
