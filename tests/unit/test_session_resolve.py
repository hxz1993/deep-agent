from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.session_scope import resolve_session
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class SessionResolveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        model = (await stores.llm_model.list_all())[0]
        self.agent_a = (
            await stores.agent.create_unique(
                AgentCreate(name="resolve-a", model_id=model.id, prompt="test")
            )
        ).id
        self.agent_b = (
            await stores.agent.create_unique(
                AgentCreate(name="resolve-b", model_id=model.id, prompt="test")
            )
        ).id

    async def asyncTearDown(self) -> None:
        await self._engine.dispose()

    async def _seed(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        await self.store.append(
            session_id,
            [ModelRequest(parts=[UserPromptPart(content="hi")])],
            agent_id=agent_id or self.agent_a,
            user_id=user_id or "user-a",
        )

    async def test_resolve_storage_ref_directly(self) -> None:
        await self._seed("sess-1")
        resolved = await resolve_session(self.store, "sess-1", agent_id=self.agent_a)
        self.assertEqual(resolved, (self.agent_a, "sess-1"))

    async def test_resolve_display_id_with_agent_id(self) -> None:
        await self._seed("sess-1")
        resolved = await resolve_session(self.store, "sess-1", agent_id=self.agent_a)
        self.assertEqual(resolved, (self.agent_a, "sess-1"))

    async def test_resolve_bare_session_without_agent(self) -> None:
        await self._seed("sess-bare")
        resolved = await resolve_session(self.store, "sess-bare")
        self.assertEqual(resolved, (self.agent_a, "sess-bare"))

    async def test_resolve_unknown_returns_none(self) -> None:
        resolved = await resolve_session(self.store, "missing", agent_id=self.agent_a)
        self.assertIsNone(resolved)

    async def test_resolve_filters_by_owner(self) -> None:
        await self._seed("owned", user_id="user-a")
        self.assertEqual(
            await resolve_session(self.store, "owned", user_id="user-a"),
            (self.agent_a, "owned"),
        )
        self.assertIsNone(await resolve_session(self.store, "owned", user_id="user-b"))

    async def test_new_event_requires_agent_and_user(self) -> None:
        message = [ModelRequest(parts=[UserPromptPart(content="hi")])]
        with self.assertRaisesRegex(ValueError, "agent_id"):
            await self.store.append("missing-agent", message, user_id="user-a")
        with self.assertRaisesRegex(ValueError, "user_id"):
            await self.store.append("missing-user", message, agent_id=self.agent_a)

    async def test_existing_session_owner_cannot_be_reassigned(self) -> None:
        await self._seed("owned", user_id="user-a")
        with self.assertRaises(PermissionError):
            await self._seed("owned", user_id="user-b")

    async def test_global_session_id_cannot_move_to_another_agent(self) -> None:
        await self._seed("sess-1")
        with self.assertRaises(PermissionError):
            await self._seed("sess-1", agent_id=self.agent_b)


if __name__ == "__main__":
    unittest.main()
