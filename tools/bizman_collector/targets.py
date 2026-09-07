from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from tools.bizman_collector.cdp import CdpEvent, CdpProtocolError
from tools.bizman_collector.discovery import ProtocolCapabilities
from tools.bizman_collector.network import FirstPartyPolicy

WarningSink = Callable[[str], None]
_ACTION_TARGET_TYPES = frozenset({"page", "iframe"})


@dataclass(slots=True)
class TargetRegistry:
    target_to_session: dict[str, str] = field(default_factory=dict)
    session_to_target: dict[str, str] = field(default_factory=dict)
    target_info: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(
        self,
        *,
        target_id: str,
        session_id: str,
        info: dict[str, Any] | None = None,
    ) -> None:
        old_session = self.target_to_session.get(target_id)
        if old_session is not None and old_session != session_id:
            self.session_to_target.pop(old_session, None)
        old_target = self.session_to_target.get(session_id)
        if old_target is not None and old_target != target_id:
            self.target_to_session.pop(old_target, None)
        self.target_to_session[target_id] = session_id
        self.session_to_target[session_id] = target_id
        if info is not None:
            self.target_info[target_id] = dict(info)

    def remove_target(self, target_id: str) -> None:
        session_id = self.target_to_session.pop(target_id, None)
        if session_id is not None:
            self.session_to_target.pop(session_id, None)
        self.target_info.pop(target_id, None)

    def remove_session(self, session_id: str) -> None:
        target_id = self.session_to_target.pop(session_id, None)
        if target_id is not None:
            self.target_to_session.pop(target_id, None)
            self.target_info.pop(target_id, None)

    def target_for_session(self, session_id: str | None) -> str | None:
        if session_id is None:
            return None
        return self.session_to_target.get(session_id)


class TargetOrchestrator:
    def __init__(
        self,
        *,
        cdp: Any,
        first_party: FirstPartyPolicy,
        capabilities: ProtocolCapabilities | None = None,
        max_post_data_size: int | None = None,
        action_binding_name: str | None = None,
        action_world_name: str | None = None,
        action_script: str | None = None,
        warning_sink: WarningSink | None = None,
    ) -> None:
        self.cdp = cdp
        self.first_party = first_party
        self.capabilities = capabilities
        self.max_post_data_size = max_post_data_size
        self.action_binding_name = action_binding_name
        self.action_world_name = action_world_name
        self.action_script = action_script
        self.warning_sink = warning_sink
        self.registry = TargetRegistry()
        self._attaching: set[str] = set()

        configured = [action_binding_name is not None, action_script is not None]
        if any(configured) and not all(configured):
            raise ValueError("action_binding_name and action_script must be configured together")

    @property
    def action_observer_enabled(self) -> bool:
        return self.action_binding_name is not None and self.action_script is not None

    def _warn(self, message: str) -> None:
        if self.warning_sink is not None:
            self.warning_sink(message)

    def _supports(self, method: str) -> bool:
        if self.capabilities is None or self.capabilities.is_empty:
            return True
        return self.capabilities.has_command(method)

    def _supports_parameter(self, method: str, parameter: str) -> bool:
        return (
            self.capabilities is not None
            and self.capabilities.command_supports_parameter(method, parameter)
        )

    def _network_enable_params(self) -> dict[str, Any]:
        if (
            self.max_post_data_size is not None
            and self.max_post_data_size >= 0
            and self._supports_parameter("Network.enable", "maxPostDataSize")
        ):
            return {"maxPostDataSize": self.max_post_data_size}
        return {}

    def _is_attachable_page(self, info: dict[str, Any]) -> bool:
        if info.get("type") != "page":
            return False
        url = info.get("url")
        return isinstance(url, str) and self.first_party.matches_url(url)

    async def bootstrap(self) -> None:
        if self._supports("Target.setDiscoverTargets"):
            await self.cdp.command(
                "Target.setDiscoverTargets",
                {"discover": True},
            )
        if not self._supports("Target.getTargets"):
            raise RuntimeError("running CDP does not support Target.getTargets")
        result = await self.cdp.command("Target.getTargets")
        infos = result.get("targetInfos", []) if isinstance(result, dict) else []
        if not isinstance(infos, list):
            return
        for info in infos:
            if isinstance(info, dict) and self._is_attachable_page(info):
                await self._attach_page(info)

    async def _attach_page(self, info: dict[str, Any]) -> None:
        target_id = info.get("targetId")
        if not isinstance(target_id, str):
            return
        if target_id in self.registry.target_to_session or target_id in self._attaching:
            self.registry.target_info[target_id] = dict(info)
            return
        if not self._supports("Target.attachToTarget"):
            raise RuntimeError("running CDP does not support Target.attachToTarget")

        self._attaching.add(target_id)
        try:
            result = await self.cdp.command(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not isinstance(session_id, str):
                raise RuntimeError(f"Target.attachToTarget returned no sessionId for {target_id}")
            self.registry.register(
                target_id=target_id,
                session_id=session_id,
                info=info,
            )
            target_type = info.get("type") if isinstance(info.get("type"), str) else None
            await self._configure_session(session_id, target_type=target_type)
        finally:
            self._attaching.discard(target_id)

    async def _configure_action_observer(
        self,
        session_id: str,
        *,
        target_type: str | None,
    ) -> None:
        if not self.action_observer_enabled or target_type not in _ACTION_TARGET_TYPES:
            return

        required = (
            "Runtime.enable",
            "Runtime.addBinding",
            "Page.addScriptToEvaluateOnNewDocument",
        )
        missing = [method for method in required if not self._supports(method)]
        if missing:
            self._warn(
                f"action observer disabled for session {session_id}: missing CDP commands {', '.join(missing)}"
            )
            return

        try:
            await self.cdp.command("Runtime.enable", session_id=session_id)
        except CdpProtocolError as exc:
            self._warn(f"Runtime.enable failed for session {session_id}: {exc}")
            return

        world_supported = bool(
            self.action_world_name
            and self._supports_parameter(
                "Page.addScriptToEvaluateOnNewDocument", "worldName"
            )
        )
        scoped_binding_supported = bool(
            world_supported
            and self._supports_parameter("Runtime.addBinding", "executionContextName")
        )

        binding_params: dict[str, Any] = {"name": self.action_binding_name}
        if scoped_binding_supported:
            binding_params["executionContextName"] = self.action_world_name
        try:
            await self.cdp.command(
                "Runtime.addBinding",
                binding_params,
                session_id=session_id,
            )
        except CdpProtocolError as exc:
            self._warn(f"Runtime.addBinding failed for session {session_id}: {exc}")
            return

        script_params: dict[str, Any] = {"source": self.action_script}
        if world_supported:
            script_params["worldName"] = self.action_world_name
        elif self.action_world_name:
            self._warn(
                f"Page.addScriptToEvaluateOnNewDocument worldName unsupported for session {session_id}; using the default world"
            )

        if self._supports_parameter(
            "Page.addScriptToEvaluateOnNewDocument", "runImmediately"
        ):
            script_params["runImmediately"] = True
        else:
            self._warn(
                f"Page.addScriptToEvaluateOnNewDocument runImmediately unsupported for session {session_id}; current document may require a future navigation before action observation begins"
            )

        try:
            await self.cdp.command(
                "Page.addScriptToEvaluateOnNewDocument",
                script_params,
                session_id=session_id,
            )
        except CdpProtocolError as exc:
            self._warn(
                f"Page.addScriptToEvaluateOnNewDocument failed for session {session_id}: {exc}"
            )

    async def _configure_session(
        self,
        session_id: str,
        *,
        target_type: str | None = None,
    ) -> None:
        if self._supports("Network.enable"):
            try:
                await self.cdp.command(
                    "Network.enable",
                    self._network_enable_params(),
                    session_id=session_id,
                )
            except CdpProtocolError as exc:
                self._warn(f"Network.enable failed for session {session_id}: {exc}")

        await self._configure_action_observer(session_id, target_type=target_type)

        if self._supports("Target.setAutoAttach"):
            try:
                await self.cdp.command(
                    "Target.setAutoAttach",
                    {
                        "autoAttach": True,
                        "waitForDebuggerOnStart": False,
                        "flatten": True,
                    },
                    session_id=session_id,
                )
            except CdpProtocolError as exc:
                self._warn(f"Target.setAutoAttach failed for session {session_id}: {exc}")

    async def handle_event(self, event: CdpEvent) -> None:
        method = event.method
        params = event.params

        if method in {"Target.targetCreated", "Target.targetInfoChanged"}:
            info = params.get("targetInfo")
            if not isinstance(info, dict):
                return
            target_id = info.get("targetId")
            if isinstance(target_id, str) and target_id in self.registry.target_to_session:
                self.registry.target_info[target_id] = dict(info)
                return
            if self._is_attachable_page(info):
                await self._attach_page(info)
            return

        if method == "Target.attachedToTarget":
            session_id = params.get("sessionId")
            info = params.get("targetInfo")
            if not isinstance(session_id, str) or not isinstance(info, dict):
                return
            target_id = info.get("targetId")
            if not isinstance(target_id, str):
                return
            if self.registry.target_for_session(session_id) is None:
                self.registry.register(
                    target_id=target_id,
                    session_id=session_id,
                    info=info,
                )
                target_type = info.get("type") if isinstance(info.get("type"), str) else None
                await self._configure_session(session_id, target_type=target_type)
            return

        if method == "Target.detachedFromTarget":
            session_id = params.get("sessionId")
            if isinstance(session_id, str):
                self.registry.remove_session(session_id)
            return

        if method == "Target.targetDestroyed":
            target_id = params.get("targetId")
            if isinstance(target_id, str):
                self.registry.remove_target(target_id)
