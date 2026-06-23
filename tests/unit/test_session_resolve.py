from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.session_key import resolve_session_key, scoped_session_key
from deep_agent.db.stores.in_memory import InMemorySessionStore


class SessionResolveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = InMemorySessionStore()

    async def _seed(self, key: str) -> None:
        await self.store.append(key, [ModelRequest(parts=[UserPromptPart(content="hi")])])

    async def test_resolve_storage_key_directly(self) -> None:
        key = scoped_session_key("agent-a", "sess-1")
        await self._seed(key)
        resolved = await resolve_session_key(self.store, key)
        self.assertEqual(resolved, key)

    async def test_resolve_display_id_with_agent_id(self) -> None:
        key = scoped_session_key("agent-a", "sess-1")
        await self._seed(key)
        resolved = await resolve_session_key(self.store, "sess-1", agent_id="agent-a")
        self.assertEqual(resolved, key)

    async def test_resolve_bare_session_without_agent(self) -> None:
        await self._seed("sess-bare")
        resolved = await resolve_session_key(self.store, "sess-bare")
        self.assertEqual(resolved, "sess-bare")

    async def test_resolve_unknown_returns_none(self) -> None:
        resolved = await resolve_session_key(self.store, "missing", agent_id="agent-a")
        self.assertIsNone(resolved)

    async def test_resolve_ambiguous_without_agent_returns_none(self) -> None:
        await self._seed(scoped_session_key("agent-a", "sess-1"))
        await self._seed(scoped_session_key("agent-b", "sess-1"))
        resolved = await resolve_session_key(self.store, "sess-1")
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
