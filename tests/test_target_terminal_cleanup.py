import unittest

from tools.bizman_collector.cdp import CdpEvent
from tools.bizman_collector.network import FirstPartyPolicy
from tools.bizman_collector.targets import TargetOrchestrator


class NoopCdp:
    async def command(self, method: str, params=None, *, session_id=None):
        return {}


class TargetTerminalCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_target_crash_removes_registry_and_configuration_state(self):
        orchestrator = TargetOrchestrator(
            cdp=NoopCdp(),
            first_party=FirstPartyPolicy(("bizmania.ru",)),
        )
        orchestrator.registry.register(
            target_id="target-1",
            session_id="session-1",
            info={
                "targetId": "target-1",
                "type": "page",
                "url": "https://bizmania.ru/",
            },
        )
        orchestrator._configured_sessions.add("session-1")
        orchestrator._action_binding_sessions.add("session-1")
        orchestrator._action_configured_sessions.add("session-1")

        await orchestrator.handle_event(
            CdpEvent(
                method="Target.targetCrashed",
                params={"targetId": "target-1"},
                session_id=None,
            )
        )

        self.assertNotIn("target-1", orchestrator.registry.target_to_session)
        self.assertNotIn("session-1", orchestrator.registry.session_to_target)
        self.assertNotIn("target-1", orchestrator.registry.target_info)
        self.assertNotIn("session-1", orchestrator._configured_sessions)
        self.assertNotIn("session-1", orchestrator._action_binding_sessions)
        self.assertNotIn("session-1", orchestrator._action_configured_sessions)


if __name__ == "__main__":
    unittest.main()
