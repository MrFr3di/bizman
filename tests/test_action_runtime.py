import unittest

from tools.bizman_collector.discovery import ProtocolCapabilities
from tools.bizman_collector.network import FirstPartyPolicy
from tools.bizman_collector.runtime import PASSIVE_CDP_METHODS
from tools.bizman_collector.targets import TargetOrchestrator


BINDING = "__bizmanActionV1"
WORLD = "bizman-action-observer-v1"
SCRIPT = "(() => {})();"


class FakeCdp:
    def __init__(self):
        self.calls: list[tuple[str, dict, str | None]] = []

    async def command(self, method: str, params=None, *, session_id=None):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {
                        "targetId": "page-1",
                        "type": "page",
                        "url": "https://bizmania.ru/company?id=1",
                    }
                ]
            }
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}


def capabilities(*, action_parameters: bool) -> ProtocolCapabilities:
    params = {
        "Network.enable": frozenset({"maxPostDataSize"}),
        "Runtime.addBinding": (
            frozenset({"name", "executionContextName"})
            if action_parameters
            else frozenset({"name"})
        ),
        "Page.addScriptToEvaluateOnNewDocument": (
            frozenset({"source", "worldName", "runImmediately"})
            if action_parameters
            else frozenset({"source"})
        ),
    }
    return ProtocolCapabilities(
        commands=frozenset(
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
        ),
        events=frozenset(
            {
                "Runtime.executionContextCreated",
                "Runtime.executionContextDestroyed",
                "Runtime.executionContextsCleared",
                "Runtime.bindingCalled",
            }
        ),
        command_parameters=params,
    )


class ActionInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_session_installs_scoped_observer_when_supported(self):
        cdp = FakeCdp()
        warnings: list[str] = []
        orchestrator = TargetOrchestrator(
            cdp=cdp,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            capabilities=capabilities(action_parameters=True),
            action_binding_name=BINDING,
            action_world_name=WORLD,
            action_script=SCRIPT,
            warning_sink=warnings.append,
        )

        await orchestrator.bootstrap()

        session_calls = [call for call in cdp.calls if call[2] == "session-1"]
        methods = [call[0] for call in session_calls]
        self.assertLess(methods.index("Network.enable"), methods.index("Runtime.enable"))
        self.assertLess(methods.index("Runtime.enable"), methods.index("Runtime.addBinding"))
        self.assertLess(
            methods.index("Runtime.addBinding"),
            methods.index("Page.addScriptToEvaluateOnNewDocument"),
        )

        binding_call = next(call for call in session_calls if call[0] == "Runtime.addBinding")
        self.assertEqual(
            binding_call[1],
            {"name": BINDING, "executionContextName": WORLD},
        )
        script_call = next(
            call
            for call in session_calls
            if call[0] == "Page.addScriptToEvaluateOnNewDocument"
        )
        self.assertEqual(
            script_call[1],
            {"source": SCRIPT, "worldName": WORLD, "runImmediately": True},
        )
        self.assertEqual(warnings, [])

    async def test_unscoped_binding_disables_observer(self):
        cdp = FakeCdp()
        warnings: list[str] = []
        orchestrator = TargetOrchestrator(
            cdp=cdp,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            capabilities=capabilities(action_parameters=False),
            action_binding_name=BINDING,
            action_world_name=WORLD,
            action_script=SCRIPT,
            warning_sink=warnings.append,
        )

        await orchestrator.bootstrap()

        session_calls = [call for call in cdp.calls if call[2] == "session-1"]
        methods = [call[0] for call in session_calls]
        self.assertNotIn("Runtime.enable", methods)
        self.assertNotIn("Runtime.addBinding", methods)
        self.assertNotIn("Page.addScriptToEvaluateOnNewDocument", methods)
        self.assertTrue(any("secure" in warning.casefold() for warning in warnings))

    def test_passive_allowlist_adds_only_observation_commands(self):
        self.assertIn("Runtime.enable", PASSIVE_CDP_METHODS)
        self.assertIn("Runtime.addBinding", PASSIVE_CDP_METHODS)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", PASSIVE_CDP_METHODS)
        self.assertNotIn("Runtime.evaluate", PASSIVE_CDP_METHODS)
        self.assertNotIn("Page.navigate", PASSIVE_CDP_METHODS)
        self.assertNotIn("Page.reload", PASSIVE_CDP_METHODS)


if __name__ == "__main__":
    unittest.main()
