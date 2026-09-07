from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from tools.bizman_collector.action_script import build_action_observer_script
from tools.bizman_collector.actions import ActionNormalizer, ExecutionContextRegistry
from tools.bizman_collector.cdp import (
    CdpConnection,
    CdpConnectionClosed,
    CdpEvent,
    CdpTransport,
    open_cdp_transport,
)
from tools.bizman_collector.correlation import ActionHttpCorrelator
from tools.bizman_collector.discovery import (
    BrowserDiscovery,
    ProtocolCapabilities,
    discover_browser,
)
from tools.bizman_collector.events import CollectorClock, EventSequencer
from tools.bizman_collector.network import FirstPartyPolicy, NetworkNormalizer
from tools.bizman_collector.storage import ArtifactStore, SessionWriter
from tools.bizman_collector.targets import TargetOrchestrator
from tools.bizman_foundation.redaction import RedactionPolicy
from tools.bizman_foundation.session import new_session_manifest

COLLECTOR_VERSION = "0.2.1"
ACTION_BINDING_NAME = "__bizmanActionV1"
ACTION_WORLD_NAME = "bizman-action-observer-v1"

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

_ACTION_REQUIRED_COMMANDS = frozenset(
    {
        "Runtime.enable",
        "Runtime.addBinding",
        "Page.addScriptToEvaluateOnNewDocument",
    }
)
_ACTION_REQUIRED_EVENTS = frozenset(
    {
        "Runtime.executionContextCreated",
        "Runtime.executionContextDestroyed",
        "Runtime.executionContextsCleared",
        "Runtime.bindingCalled",
    }
)
_ACTION_REQUIRED_PARAMETERS = {
    "Runtime.addBinding": frozenset({"executionContextName"}),
    "Page.addScriptToEvaluateOnNewDocument": frozenset({"worldName"}),
}

TransportFactory = Callable[[str], AbstractAsyncContextManager[CdpTransport]]


class CollectorEventPipeline:
    """Normalize observations, persist them, then emit immutable correlations."""

    def __init__(
        self,
        *,
        binding_name: str,
        observer_world_name: str,
        first_party: FirstPartyPolicy,
        contexts: ExecutionContextRegistry,
        action_normalizer: ActionNormalizer,
        network_normalizer: NetworkNormalizer,
        correlator: ActionHttpCorrelator,
        writer: Any,
    ) -> None:
        self.binding_name = binding_name
        self.observer_world_name = observer_world_name
        self.first_party = first_party
        self.contexts = contexts
        self.action_normalizer = action_normalizer
        self.network_normalizer = network_normalizer
        self.correlator = correlator
        self.writer = writer

    def _append_observation(self, event: dict[str, Any] | None) -> None:
        if event is None:
            return
        self.writer.append_event(event)
        for link in self.correlator.observe(event):
            self.writer.append_event(link)

    def handle_network(self, event: CdpEvent, *, target_id: str | None) -> None:
        normalized = self.network_normalizer.normalize(
            method=event.method,
            params=event.params,
            target_id=target_id,
        )
        self._append_observation(normalized)

    def handle_runtime(self, event: CdpEvent, *, target_id: str | None) -> None:
        session_id = event.session_id
        if not isinstance(session_id, str):
            return
        params = event.params

        if event.method == "Runtime.executionContextCreated":
            context = params.get("context")
            if not isinstance(context, dict):
                return
            context_id = context.get("id")
            if isinstance(context_id, bool) or not isinstance(context_id, int):
                return
            aux_data = context.get("auxData")
            frame_id = None
            if isinstance(aux_data, dict) and isinstance(aux_data.get("frameId"), str):
                frame_id = aux_data["frameId"]
            world_name = context.get("name") if isinstance(context.get("name"), str) else None
            origin = context.get("origin") if isinstance(context.get("origin"), str) else None
            self.contexts.register(
                session_id=session_id,
                context_id=context_id,
                frame_id=frame_id,
                world_name=world_name,
                origin=origin,
            )
            return

        if event.method == "Runtime.executionContextDestroyed":
            context_id = params.get("executionContextId")
            if isinstance(context_id, int) and not isinstance(context_id, bool):
                self.contexts.remove_context(session_id, context_id)
            return

        if event.method == "Runtime.executionContextsCleared":
            self.contexts.clear_session(session_id)
            return

        if event.method != "Runtime.bindingCalled":
            return
        if params.get("name") != self.binding_name:
            return
        payload = params.get("payload")
        context_id = params.get("executionContextId")
        if not isinstance(payload, str):
            return
        if isinstance(context_id, bool) or not isinstance(context_id, int):
            return

        if self.contexts.world_for(session_id, context_id) != self.observer_world_name:
            return
        context_origin = self.contexts.origin_for(session_id, context_id)
        if context_origin is None or not self.first_party.matches_url(context_origin):
            return

        normalized = self.action_normalizer.normalize_binding(
            payload,
            target_id=target_id,
            frame_id=self.contexts.frame_for(session_id, context_id),
        )
        self._append_observation(normalized)

    def clear_session(self, session_id: str | None) -> None:
        if isinstance(session_id, str):
            self.contexts.clear_session(session_id)


async def _discover(endpoint: str) -> BrowserDiscovery:
    return await asyncio.to_thread(discover_browser, endpoint)


async def _consume_events(
    *,
    cdp: CdpConnection,
    orchestrator: TargetOrchestrator,
    pipeline: CollectorEventPipeline,
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
            terminal_session: str | None = None
            if event.method == "Target.detachedFromTarget":
                detached = event.params.get("sessionId")
                if isinstance(detached, str):
                    terminal_session = detached
            elif event.method in {"Target.targetDestroyed", "Target.targetCrashed"}:
                target_id = event.params.get("targetId")
                if isinstance(target_id, str):
                    terminal_session = orchestrator.registry.target_to_session.get(target_id)

            await orchestrator.handle_event(event)
            pipeline.clear_session(terminal_session)
            continue

        target_id = orchestrator.registry.target_for_session(event.session_id)
        if event.method.startswith("Runtime."):
            pipeline.handle_runtime(event, target_id=target_id)
            continue
        if event.method.startswith("Network."):
            pipeline.handle_network(event, target_id=target_id)


def _action_instrumentation_issues(
    capabilities: ProtocolCapabilities,
) -> tuple[str, ...]:
    if capabilities.is_empty:
        return ("running CDP reported no capabilities",)

    issues: list[str] = []
    for method in sorted(_ACTION_REQUIRED_COMMANDS):
        if not capabilities.has_command(method):
            issues.append(f"missing command {method}")
    for event in sorted(_ACTION_REQUIRED_EVENTS):
        if not capabilities.has_event(event):
            issues.append(f"missing event {event}")
    for method, parameters in _ACTION_REQUIRED_PARAMETERS.items():
        for parameter in sorted(parameters):
            if not capabilities.command_supports_parameter(method, parameter):
                issues.append(f"{method} lacks parameter {parameter}")
    return tuple(issues)


def _action_instrumentation_supported(discovery: BrowserDiscovery) -> bool:
    return not _action_instrumentation_issues(discovery.capabilities)


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
    contexts = ExecutionContextRegistry()
    network_normalizer = NetworkNormalizer(
        session_id=writer.session_id,
        first_party=first_party,
        redaction=redaction,
        artifacts=artifacts,
        sequencer=sequencer,
        clock=clock,
    )
    action_normalizer = ActionNormalizer(
        session_id=writer.session_id,
        redaction=redaction,
        sequencer=sequencer,
        clock=clock,
    )
    correlator = ActionHttpCorrelator(
        session_id=writer.session_id,
        sequencer=sequencer,
        clock=clock,
    )
    pipeline = CollectorEventPipeline(
        binding_name=ACTION_BINDING_NAME,
        observer_world_name=ACTION_WORLD_NAME,
        first_party=first_party,
        contexts=contexts,
        action_normalizer=action_normalizer,
        network_normalizer=network_normalizer,
        correlator=correlator,
        writer=writer,
    )

    action_issues = _action_instrumentation_issues(resolved_discovery.capabilities)
    action_supported = not action_issues
    if action_issues:
        writer.add_warning(
            "DOM action observer disabled: " + "; ".join(action_issues)
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
                action_binding_name=ACTION_BINDING_NAME if action_supported else None,
                action_world_name=ACTION_WORLD_NAME if action_supported else None,
                action_script=(
                    build_action_observer_script(
                        ACTION_BINDING_NAME,
                        first_party.hosts,
                    )
                    if action_supported
                    else None
                ),
                warning_sink=writer.add_warning,
            )

            async with asyncio.TaskGroup() as group:
                group.create_task(cdp.receive_loop(), name="bizman-cdp-receiver")
                await orchestrator.bootstrap()
                group.create_task(
                    _consume_events(
                        cdp=cdp,
                        orchestrator=orchestrator,
                        pipeline=pipeline,
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
