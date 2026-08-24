"""会话标题生成与回显集成测试。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.auxiliary_model import AuxiliaryModelUpdate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import (
    AUX_TITLE_MODEL_NAME,
    ensure_title_uses_main_model,
    get_default_test_provider_id,
    make_sqlite_stores,
    seed_auxiliary_model_configs,
)
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.live_llm import skip_unless_title_llm, title_llm_configured
from tests.integration.helpers.models import text_only_model
from tests.integration.helpers.session_seed import (
    load_session_fixture,
    seed_session_from_fixture,
    wait_for_session_title,
)
from tests.integration.helpers.title_assertions import (
    assert_fallback_title,
    assert_title_differs_from_prompt,
    assert_title_keywords,
    assert_title_reasonable,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"
_DEEPCUBE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deepcube_tiered_rebate_session.json"
_CHAT_JSON = {"capabilities": []}


class SessionTitleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, self._session_factory = await make_sqlite_stores()
        model_row = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=await get_default_test_provider_id(stores),
                name="chat-model",
                model="test:chat-model",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
        )
        self._model_id = model_row.id
        self._stores = stores
        self._agent_id = (
            await stores.agent.create_unique(
                AgentCreate(name="title-agent", model_id=model_row.id, prompt="helpful assistant"),
            )
        ).id
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

    async def _apply_title_env_to_store(self) -> None:
        await seed_auxiliary_model_configs(self._stores)

    async def _stream_first_turn(self, *, message: str, session_id: str | None = None) -> tuple[str, list[dict]]:
        body = {
            **_CHAT_JSON,
            "message": message,
            "agent_id": self._agent_id,
        }
        if session_id is not None:
            body["session_id"] = session_id
        with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model("好的，我来帮你处理。")):
            with mock_request_disconnect():
                async with AsyncClient(transport=self._transport, base_url="http://test", timeout=30.0) as client:
                    async with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])
        events = parse_sse_events(raw)
        resolved_session_id = events[0]["session_id"]
        await get_context().event_persister.flush(resolved_session_id, agent_id=self._agent_id)
        return resolved_session_id, events

    async def test_legacy_session_without_title_returns_user_prompt(self) -> None:
        fixture = load_session_fixture(_FIXTURES / "legacy_no_title.json")
        seeded = await seed_session_from_fixture(self._session_factory, fixture)

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            list_resp = await client.get("/api/v1/sessions", params={"agent_id": seeded.agent_id})
            detail_resp = await client.get(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
            )

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(detail_resp.status_code, 200)
        listed = next(item for item in list_resp.json()["items"] if item["session_id"] == seeded.session_id)
        detail = detail_resp.json()
        self.assertIsNone(listed["title"])
        self.assertIsNone(detail["title"])
        self.assertIn("快速排序", listed["user_prompt"] or "")
        self.assertEqual(listed["user_prompt"], detail["user_prompt"])

    async def test_manual_title_is_not_overwritten_by_new_chat_turn(self) -> None:
        session_id, _ = await self._stream_first_turn(message="第一条消息")
        await get_context().event_persister.flush(session_id, agent_id=self._agent_id)
        manual_title = "手动固定标题"
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            patch_resp = await client.patch(
                f"/api/v1/sessions/{session_id}",
                params={"agent_id": self._agent_id},
                json={"title": manual_title},
            )
            self.assertEqual(patch_resp.status_code, 200)

        await self._stream_first_turn(message="第二条消息", session_id=session_id)
        title = await get_context().store.get_session_title(session_id, agent_id=self._agent_id)
        self.assertEqual(title, manual_title)

    async def test_auto_rename_uses_main_model_when_title_model_unconfigured(self) -> None:
        fixture = load_session_fixture(_FIXTURES / "legacy_no_title.json")
        seeded = await seed_session_from_fixture(self._session_factory, fixture)
        await ensure_title_uses_main_model(self._stores)

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("阶梯返利计算说明"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                resp = await client.patch(
                    f"/api/v1/sessions/{seeded.session_id}",
                    params={"agent_id": seeded.agent_id},
                    json={"auto": True},
                )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["title"], "阶梯返利计算说明")
        assert_title_differs_from_prompt(body["title"], body["user_prompt"])

    async def test_auto_rename_falls_back_to_user_prompt_without_any_model(self) -> None:
        fixture = load_session_fixture(_FIXTURES / "legacy_no_title.json")
        seeded = await seed_session_from_fixture(self._session_factory, fixture)
        await ensure_title_uses_main_model(self._stores)

        with patch("deep_agent.agent.factory._resolve_model", return_value="unconfigured:model"):
            with patch(
                "deep_agent.agent.title_generator.generate_title",
                return_value=None,
            ):
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    resp = await client.patch(
                        f"/api/v1/sessions/{seeded.session_id}",
                        params={"agent_id": seeded.agent_id},
                        json={"auto": True},
                    )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        assert_fallback_title(body["title"], body["user_prompt"])

    @skip_unless_title_llm
    async def test_first_chat_turn_generates_ai_title(self) -> None:
        await self._apply_title_env_to_store()
        session_id, _ = await self._stream_first_turn(message="请解释 Python 装饰器的作用")
        title = await wait_for_session_title(
            get_context().store,
            session_id,
            agent_id=self._agent_id,
            timeout=45.0,
        )
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            detail = (
                await client.get(
                    f"/api/v1/sessions/{session_id}",
                    params={"agent_id": self._agent_id},
                )
            ).json()
        assert_title_reasonable(title)
        assert_title_differs_from_prompt(title, detail["user_prompt"])

    @skip_unless_title_llm
    async def test_deepcube_fixture_auto_rename_uses_live_title_llm(self) -> None:
        await self._apply_title_env_to_store()
        fixture = load_session_fixture(_DEEPCUBE_FIXTURE)
        seeded = await seed_session_from_fixture(
            self._session_factory,
            fixture,
            model="deepseek:deepseek-v4-flash",
        )

        async with AsyncClient(transport=self._transport, base_url="http://test", timeout=60.0) as client:
            resp = await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"auto": True},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        assert_title_reasonable(body["title"])
        assert_title_differs_from_prompt(body["title"], body["user_prompt"])
        assert_title_keywords(body["title"], "返利", "阶梯", "销售额", "销售")

    @unittest.skipUnless(title_llm_configured(), "optional live smoke for full deepcube fixture")
    async def test_deepcube_full_fixture_auto_rename_live_smoke(self) -> None:
        """Slow opt-in smoke using the exported 12-message session when available."""
        full_fixture_path = Path(__file__).resolve().parent / "fixtures" / "deepcube_tiered_rebate_full.json"
        await self._apply_title_env_to_store()
        fixture = load_session_fixture(full_fixture_path)
        seeded = await seed_session_from_fixture(self._session_factory, fixture)
        async with AsyncClient(transport=self._transport, base_url="http://test", timeout=90.0) as client:
            resp = await client.patch(
                f"/api/v1/sessions/{seeded.session_id}",
                params={"agent_id": seeded.agent_id},
                json={"auto": True},
            )
        self.assertEqual(resp.status_code, 200)
        assert_title_reasonable(resp.json()["title"])


class AuxiliaryModelApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, self._session_factory = await make_sqlite_stores()
        self._stores = stores
        await stores.auxiliary_model.update("title", AuxiliaryModelUpdate(llm_model_id=None))
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

    async def test_list_defaults_to_auto_title_row(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auxiliary-models")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        title_row = next(item for item in items if item["use_case"] == "title")
        self.assertEqual(title_row["mode"], "auto")
        self.assertIsNone(title_row["llm_model_id"])

    async def test_ensure_defaults_removes_obsolete_context_summary_row(self) -> None:
        await self._stores.auxiliary_model.update(
            "context_summary",
            AuxiliaryModelUpdate(llm_model_id=None),
        )

        await self._stores.auxiliary_model.ensure_defaults()

        rows = await self._stores.auxiliary_model.list_all()
        self.assertEqual([row.use_case for row in rows], ["title"])

    async def test_update_title_model_from_env_seed(self) -> None:
        if not os.environ.get("DEEP_AGENT_TITLE_MODEL"):
            self.skipTest("DEEP_AGENT_TITLE_MODEL not set")
        await seed_auxiliary_model_configs(self._stores)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auxiliary-models/title")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["mode"], "dedicated")
        self.assertEqual(body["llm_model_name"], AUX_TITLE_MODEL_NAME)


if __name__ == "__main__":
    unittest.main()
