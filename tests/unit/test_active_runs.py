from __future__ import annotations

import unittest

from deep_agent.agent.active_runs import ActiveRunRegistry


class ActiveRunRegistryTests(unittest.TestCase):
    def test_register_cancel_and_unregister(self) -> None:
        registry = ActiveRunRegistry()
        entry = registry.register(
            agent_id="agent-1",
            session_id="session-1",
            user_id="user-1",
        )

        self.assertIs(registry.get(agent_id="agent-1", session_id="session-1"), entry)
        self.assertFalse(
            registry.request_cancel(
                agent_id="agent-1",
                session_id="session-1",
                user_id="other-user",
            )
        )
        self.assertTrue(
            registry.request_cancel(
                agent_id="agent-1",
                session_id="session-1",
                user_id="user-1",
            )
        )
        self.assertEqual(entry.status, "cancel_requested")

        registry.unregister(entry)
        self.assertIsNone(registry.get(agent_id="agent-1", session_id="session-1"))

    def test_repeated_cancel_and_cancel_all_are_idempotent(self) -> None:
        registry = ActiveRunRegistry()
        registry.register(agent_id=None, session_id="session-1", user_id="user-1")
        self.assertTrue(
            registry.request_cancel(agent_id=None, session_id="session-1", user_id="user-1")
        )
        self.assertTrue(
            registry.request_cancel(agent_id=None, session_id="session-1", user_id="user-1")
        )
        registry.cancel_all()
        registry.cancel_all()
        self.assertIsNone(registry.get(agent_id=None, session_id="session-1"))

