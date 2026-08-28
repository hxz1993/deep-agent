"""会话重命名功能回归测试。

覆盖:
- PATCH /api/v1/sessions/{id} 手动重命名（含 happy path）
- PATCH /api/v1/sessions/{id} 不再接受自动重命名
- 参数校验与 404
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.session_seed import load_session_fixture, seed_session_from_fixture

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"


class SessionRenameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, self._session_factory = await make_sqlite_stores()
        self._app = FastAPI()
        install_test_user_header(self._app)
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=self._session_factory,
            )
        )
        self._transport = ASGITransport(app=self._app)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _seed_legacy_session(self):
        fixture = load_session_fixture(_FIXTURES / "legacy_no_title.json")
        return await seed_session_from_fixture(self._session_factory, fixture)

    async def test_manual_rename_updates_title_and_preserves_user_prompt(self) -> None:
        """手动重命名应写库并在 PATCH 响应中回显 title + user_prompt。"""
        seeded = await self._seed_legacy_session()
        manual_title = "快速排序笔记"

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            patch_resp = await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"title": manual_title},
            )
            self.assertEqual(patch_resp.status_code, 200)
            body = patch_resp.json()

            get_resp = await client.get(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
            )

        self.assertEqual(body["title"], manual_title)
        self.assertIn("快速排序", body["user_prompt"] or "")
        self.assertEqual(body["user_prompt"], get_resp.json()["user_prompt"])
        self.assertEqual(get_resp.json()["title"], manual_title)

        stored = await get_context().store.get_session_title(
            seeded.session_id,
            agent_id=seeded.agent_id,
        )
        self.assertEqual(stored, manual_title)
        title_events = await get_context().store.list_events(
            seeded.session_id,
            event_type="session/title",
        )
        self.assertEqual(len(title_events), 1)
        self.assertEqual(title_events[0].payload["source"], "user")
        self.assertEqual(title_events[0].payload["title"], manual_title)

    async def test_manual_rename_whitespace_is_rejected(self) -> None:
        """空白 title 不支持清空或重置。"""
        seeded = await self._seed_legacy_session()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"title": "临时标题"},
            )
            clear_resp = await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"title": "   "},
            )

        self.assertEqual(clear_resp.status_code, 422)

    async def test_auto_rename_is_removed(self) -> None:
        """auto=true 不再属于 PATCH 契约。"""
        seeded = await self._seed_legacy_session()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"auto": True},
            )
        self.assertEqual(resp.status_code, 422)

    async def test_rename_conflict(self) -> None:
        """同时指定 title 和 auto 应该失败。"""
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/sessions/test-session",
                json={"title": "新标题", "auto": True},
            )
            self.assertEqual(resp.status_code, 422)

    async def test_rename_no_params_fails(self) -> None:
        """不指定 title 应该失败。"""
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/sessions/test-session",
                json={},
            )
            self.assertEqual(resp.status_code, 422)

    async def test_rename_nonexistent_session(self) -> None:
        """重命名不存在的会话应该返回 404。"""
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/sessions/nonexistent",
                json={"title": "新标题"},
            )
            self.assertEqual(resp.status_code, 404)

    async def test_rename_empty_title_is_rejected_before_lookup(self) -> None:
        """空白 title 在会话查询前就被拒绝。"""
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/sessions/test",
                json={"title": "  "},
            )
            self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
