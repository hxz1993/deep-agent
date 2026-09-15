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

        self.assertEqual(registry.active_count(), 1)
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
        self.assertEqual(registry.active_count(), 0)
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
        self.assertEqual(registry.active_count(), 0)
        self.assertIsNone(registry.get(agent_id=None, session_id="session-1"))


class ActiveRunDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_fences_only_matching_admission_and_stale_requests(self):
        registry = ActiveRunRegistry()
        before = registry.generation
        async with registry.deleting(agent_id="target"):
            with self.assertRaises(RuntimeError):
                registry.register(agent_id="target", session_id="new", user_id="owner", expected_generation=before)
            unrelated = registry.register(agent_id="other", session_id="other-session", user_id="owner", expected_generation=before)
            registry.unregister(unrelated)
        with self.assertRaises(RuntimeError):
            registry.register(agent_id="target", session_id="new", user_id="owner", expected_generation=before)
        unrelated = registry.register(agent_id="other", session_id="other-session", user_id="owner", expected_generation=before)
        registry.unregister(unrelated)
        fresh = registry.register(agent_id="target", session_id="new", user_id="owner", expected_generation=registry.generation)
        registry.unregister(fresh)

    async def test_channel_delete_does_not_cancel_agent_console_runs(self):
        import asyncio
        registry = ActiveRunRegistry()
        console = registry.register(agent_id="agent", session_id="console", user_id="owner")
        channel = registry.register(agent_id="agent", session_id="channel", user_id="other", channel_account_id="target")
        async def cleanup():
            while not channel.cancellation_token.cancelled:
                await asyncio.sleep(.001)
            self.assertFalse(console.cancellation_token.cancelled)
            registry.unregister(channel)
        task = asyncio.create_task(cleanup())
        async with registry.deleting(channel_account_id="target"):
            self.assertTrue(channel.finished.is_set())
        await task
        registry.unregister(console)

    async def test_timeout_keeps_registration_and_releases_deletion_gate(self):
        registry = ActiveRunRegistry()
        entry = registry.register(agent_id="agent", session_id="session", user_id="owner")
        with self.assertRaises(TimeoutError):
            async with registry.deleting(agent_id="agent", timeout=.01):
                self.fail("unconfirmed stop must never delete")
        self.assertIs(registry.get(agent_id="agent", session_id="session"), entry)
        registry.unregister(entry)
        fresh = registry.register(agent_id="agent", session_id="session", user_id="owner", expected_generation=registry.generation)
        registry.unregister(fresh)
