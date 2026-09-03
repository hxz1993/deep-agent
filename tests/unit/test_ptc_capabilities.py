from __future__ import annotations

import unittest

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.schemas.agent import AgentCreate, AgentUpdate


class PtcCapabilityTests(unittest.TestCase):
    def test_legacy_code_is_canonicalized_and_alias_duplicates_are_stable(self) -> None:
        self.assertEqual(
            canonical_tool_package_codes(
                ["mcp:demo", "builtin:code-mode", "builtin:ptc"]
            ),
            ["mcp:demo", "builtin:ptc"],
        )

    def test_agent_create_and_update_persist_canonical_code(self) -> None:
        created = AgentCreate(
            name="demo",
            model_id="model",
            prompt="prompt",
            capabilities=["builtin:code-mode"],
        )
        updated = AgentUpdate(capabilities=["builtin:code-mode"])

        self.assertEqual(created.capabilities, ["builtin:ptc"])
        self.assertEqual(updated.capabilities, ["builtin:ptc"])

    def test_runtime_spec_can_be_canonicalized_without_mutating_other_codes(self) -> None:
        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="prompt",
            capabilities=canonical_tool_package_codes(
                ["builtin:code-mode", "mcp:demo"]
            ),
        )
        self.assertEqual(spec.capabilities, ["builtin:ptc", "mcp:demo"])


if __name__ == "__main__":
    unittest.main()
