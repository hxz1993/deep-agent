from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.session_scope import resolve_session
from tests.helpers.sqlite_stores import make_sqlite_stores


class SessionResolveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator

    async def asyncTearDown(self) -> None:
        await self._engine.dispose()

    async def _seed(self, session_id: str, *, agent_id: str | None = None) -> None:
        await self.store.append(
            session_id,
            [ModelRequest(parts=[UserPromptPart(content="hi")])],
            agent_id=agent_id,
        )

    async def test_resolve_storage_ref_directly(self) -> None:
        await self._seed("sess-1", agent_id="agent-a")
        resolved = await resolve_session(self.store, "sess-1", agent_id="agent-a")
        self.assertEqual(resolved, ("agent-a", "sess-1"))

    async def test_resolve_display_id_with_agent_id(self) -> None:
        await self._seed("sess-1", agent_id="agent-a")
        resolved = await resolve_session(self.store, "sess-1", agent_id="agent-a")
        self.assertEqual(resolved, ("agent-a", "sess-1"))

    async def test_resolve_bare_session_without_agent(self) -> None:
        await self._seed("sess-bare")
        resolved = await resolve_session(self.store, "sess-bare")
        self.assertEqual(resolved, ("", "sess-bare"))

    async def test_resolve_unknown_returns_none(self) -> None:
        resolved = await resolve_session(self.store, "missing", agent_id="agent-a")
        self.assertIsNone(resolved)

    async def test_resolve_ambiguous_without_agent_returns_none(self) -> None:
        await self._seed("sess-1", agent_id="agent-a")
        await self._seed("sess-1", agent_id="agent-b")
        resolved = await resolve_session(self.store, "sess-1")
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
