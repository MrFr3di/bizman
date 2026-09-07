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
        self._configured_sessions: set[str] = set()
        self._action_binding_sessions: set[str] = set()
        self._action_configured_sessions: set[str] = set()

        configured = [
            action_binding_name is not None,
            action_world_name is not None,
            action_script is not None,
        ]
        if any(configured) and not all(configured):
            raise ValueError(
                "action_binding_name, action_world_name and action_script must be configured together"
            )

    @property
    def action_observer_enabled(self) -> bool:
        return (
            self.action_binding_name is not None
            and self.action_world_name is not None
            and self.action_script is not None
        )

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

    def _is_first_party_target(self, info: dict[str, Any]) -> bool:
        url = info.get("url")
        return isinstance(url, str) and self.first_party.matches_url(url)

    @staticmethod
    def _has_explicit_target_url(info: dict[str, Any]) -> bool:
        url = info.get("url")
        return isinstance(url, str) and bool(url.strip())

    def _is_attachable_page(self, info: dict[str, Any]) -> bool:
        return info.get("type") == "page" and self._is_first_party_target(info)

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
            await self._configure_session(
                session_id,
                target_type=target_type,
                allow_action=True,
            )
        finally:
            self._attaching.discard(target_id)

    async def _configure_action_observer(
        self,
        session_id: str,
        *,
        target_type: str | None,
    ) -> bool:
        if not self.action_observer_enabled or target_type not in _ACTION_TARGET_TYPES:
            return False

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
            return False

        secure_world = self._supports_parameter(
            "Page.addScriptToEvaluateOnNewDocument", "worldName"
        )
        secure_binding = self._supports_parameter(
            "Runtime.addBinding", "executionContextName"
        )
        if not secure_world or not secure_binding:
            self._warn(
                f"action observer disabled for session {session_id}: secure isolated-world binding scope is unsupported"
            )
            return False

        if session_id not in self._action_binding_sessions:
            try:
                await self.cdp.command("Runtime.enable", session_id=session_id)
            except CdpProtocolError as exc:
                self._warn(f"Runtime.enable failed for session {session_id}: {exc}")
                return False

            try:
                await self.cdp.command(
                    "Runtime.addBinding",
                    {
                        "name": self.action_binding_name,
                        "executionContextName": self.action_world_name,
                    },
                    session_id=session_id,
                )
            except CdpProtocolError as exc:
                self._warn(f"Runtime.addBinding failed for session {session_id}: {exc}")
                return False
            self._action_binding_sessions.add(session_id)

        script_params: dict[str, Any] = {
            "source": self.action_script,
            "worldName": self.action_world_name,
        }
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
            return False
        return True

    async def _configure_session(
        self,
        session_id: str,
        *,
        target_type: str | None = None,
        allow_action: bool = False,
    ) -> None:
        first_setup = session_id not in self._configured_sessions
        if first_setup:
            self._configured_sessions.add(session_id)
            if self._supports("Network.enable"):
                try:
                    await self.cdp.command(
                        "Network.enable",
                        self._network_enable_params(),
                        session_id=session_id,
                    )
                except CdpProtocolError as exc:
                    self._warn(f"Network.enable failed for session {session_id}: {exc}")

        if (
            allow_action
            and target_type in _ACTION_TARGET_TYPES
            and session_id not in self._action_configured_sessions
        ):
            configured = await self._configure_action_observer(
                session_id,
                target_type=target_type,
            )
            if configured:
                self._action_configured_sessions.add(session_id)

        if first_setup and self._supports("Target.setAutoAttach"):
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

    def _remove_terminal_target(self, target_id: str) -> None:
        session_id = self.registry.target_to_session.get(target_id)
        self.registry.remove_target(target_id)
        if isinstance(session_id, str):
            self._configured_sessions.discard(session_id)
            self._action_binding_sessions.discard(session_id)
            self._action_configured_sessions.discard(session_id)

    async def handle_event(self, event: CdpEvent) -> None:
        method = event.method
        params = event.params

        if method in {"Target.targetCreated", "Target.targetInfoChanged"}:
            info = params.get("targetInfo")
            if not isinstance(info, dict):
                return
            target_id = info.get("targetId")
            if isinstance(target_id, str) and target_id in self.registry.target_to_session:
                session_id = self.registry.target_to_session[target_id]
                self.registry.target_info[target_id] = dict(info)
                if self._is_first_party_target(info):
                    target_type = (
                        info.get("type") if isinstance(info.get("type"), str) else None
                    )
                    await self._configure_session(
                        session_id,
                        target_type=target_type,
                        allow_action=True,
                    )
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
                if self._is_first_party_target(info):
                    target_type = (
                        info.get("type") if isinstance(info.get("type"), str) else None
                    )
                    await self._configure_session(
                        session_id,
                        target_type=target_type,
                        allow_action=True,
                    )
                elif not self._has_explicit_target_url(info):
                    # Auto-attached workers/frames can initially have an empty URL.
                    # Keep passive Network/Target coverage, but do not install any
                    # DOM observer until a later targetInfoChanged proves the target
                    # is first-party.
                    await self._configure_session(
                        session_id,
                        target_type=None,
                        allow_action=False,
                    )
            return

        if method == "Target.detachedFromTarget":
            session_id = params.get("sessionId")
            if isinstance(session_id, str):
                self.registry.remove_session(session_id)
                self._configured_sessions.discard(session_id)
                self._action_binding_sessions.discard(session_id)
                self._action_configured_sessions.discard(session_id)
            return

        if method in {"Target.targetDestroyed", "Target.targetCrashed"}:
            target_id = params.get("targetId")
            if isinstance(target_id, str):
                self._remove_terminal_target(target_id)
            return
