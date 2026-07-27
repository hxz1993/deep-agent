"""LLM model metadata CRUD + modality resolution chain integration tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent.attachments import model_supports_image_input
from deep_agent.agent.models.modality_resolver import resolve_input_modalities
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.models_dev import mock_fetch_models_dev_modalities


class LlmModelModalitiesIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        self._app = FastAPI()
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
            )
        )
        self._transport = ASGITransport(app=self._app)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_crud_and_user_override_resolution(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/llm-models",
                json={"name": "private-model", "model": "private:my-model"},
            )
            self.assertEqual(create_resp.status_code, 201)
            created = create_resp.json()
            model_id = created["id"]

            patch_resp = await client.patch(
                f"/api/v1/llm-models/{model_id}",
                json={"input_modalities": ["text", "pdf"]},
            )
            self.assertEqual(patch_resp.status_code, 200)
            patched = patch_resp.json()
            self.assertEqual(patched["input_modalities"], ["text", "pdf"])
            self.assertEqual(patched["capabilities_source"], "user")

            get_resp = await client.get(f"/api/v1/llm-models/{model_id}")
            self.assertEqual(get_resp.status_code, 200)
            listed = await client.get("/api/v1/llm-models")
            self.assertTrue(any(row["id"] == model_id for row in listed.json()))

            resolved = resolve_input_modalities(
                patched["model"],
                user_override=patched["input_modalities"],
                capabilities_source=patched["capabilities_source"],
            )
            self.assertEqual(resolved, ["text", "pdf"])
            self.assertFalse(
                model_supports_image_input(
                    patched["model"],
                    db_modalities=patched["input_modalities"],
                    capabilities_source=patched["capabilities_source"],
                )
            )

            delete_resp = await client.delete(f"/api/v1/llm-models/{model_id}")
            self.assertEqual(delete_resp.status_code, 200)
            missing = await client.get(f"/api/v1/llm-models/{model_id}")
            self.assertEqual(missing.status_code, 404)

    async def test_create_with_modalities_sets_user_source(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/llm-models",
                json={
                    "name": "create-with-modalities",
                    "model": "private:create-modalities",
                    "input_modalities": ["text", "image"],
                },
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["input_modalities"], ["text", "image"])
        self.assertEqual(body["capabilities_source"], "user")

    @patch(
        "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
        new=mock_fetch_models_dev_modalities,
    )
    async def test_create_auto_refreshes_modalities_on_insert(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/llm-models",
                json={"name": "auto-on-create", "model": "openai:online-only-model"},
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["input_modalities"], ["text", "image"])
        self.assertEqual(body["capabilities_source"], "models_dev")

    async def test_create_succeeds_when_optional_metadata_refresh_fails(self) -> None:
        with patch(
            "deep_agent.api.v1.llm_models._row_after_auto_refresh",
            side_effect=RuntimeError("metadata refresh failed"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/llm-models",
                    json={"name": "saved-before-refresh", "model": "private:model"},
                )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "saved-before-refresh")
        self.assertIsNotNone(await get_context().stores.llm_model.get_by_name("saved-before-refresh"))

    @patch(
        "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
        new=mock_fetch_models_dev_modalities,
    )
    async def test_refresh_metadata_prefers_models_dev_snapshot(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/v1/llm-models",
                json={"name": "online-model", "model": "openai:online-only-model"},
            )
            model_id = create_resp.json()["id"]

            refresh_resp = await client.post(f"/api/v1/llm-models/{model_id}/refresh-metadata")
            self.assertEqual(refresh_resp.status_code, 200)
            body = refresh_resp.json()
            self.assertEqual(body["input_modalities"], ["text", "image"])
            self.assertEqual(body["capabilities_source"], "models_dev")

            resolved = resolve_input_modalities(
                body["model"],
                user_override=body["input_modalities"],
                capabilities_source=body["capabilities_source"],
            )
            self.assertEqual(resolved, ["text", "image"])
            self.assertTrue(
                model_supports_image_input(
                    body["model"],
                    db_modalities=body["input_modalities"],
                    capabilities_source=body["capabilities_source"],
                )
            )

    async def test_refresh_metadata_uses_bundled_snapshot_when_models_dev_missing(self) -> None:
        with patch(
            "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
            return_value=None,
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                create_resp = await client.post(
                    "/api/v1/llm-models",
                    json={"name": "gpt-4o", "model": "openai:gpt-4o"},
                )
                model_id = create_resp.json()["id"]
                refresh_resp = await client.post(f"/api/v1/llm-models/{model_id}/refresh-metadata")
        self.assertEqual(refresh_resp.status_code, 200)
        body = refresh_resp.json()
        self.assertEqual(body["input_modalities"], ["text", "image", "pdf"])
        self.assertEqual(body["capabilities_source"], "bundled")

    async def test_refresh_metadata_uses_name_heuristic_for_unknown_model(self) -> None:
        with patch(
            "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
            return_value=None,
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                create_resp = await client.post(
                    "/api/v1/llm-models",
                    json={"name": "unknown", "model": "vendor:totally-unknown-model"},
                )
                model_id = create_resp.json()["id"]
                refresh_resp = await client.post(f"/api/v1/llm-models/{model_id}/refresh-metadata")
        body = refresh_resp.json()
        self.assertEqual(body["input_modalities"], ["text"])
        self.assertEqual(body["capabilities_source"], "heuristic")

    @patch(
        "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
        new=mock_fetch_models_dev_modalities,
    )
    async def test_refresh_all_metadata_skips_user_override_rows(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            user_resp = await client.post(
                "/api/v1/llm-models",
                json={"name": "user-owned", "model": "openai:online-only-model"},
            )
            user_id = user_resp.json()["id"]
            await client.patch(
                f"/api/v1/llm-models/{user_id}",
                json={"input_modalities": ["text"]},
            )

            auto_resp = await client.post(
                "/api/v1/llm-models",
                json={"name": "auto-owned", "model": "openai:online-only-model"},
            )
            auto_id = auto_resp.json()["id"]

            batch_resp = await client.post("/api/v1/llm-models/refresh-metadata")
            self.assertEqual(batch_resp.status_code, 200)
            self.assertGreaterEqual(batch_resp.json()["updated"], 1)

            user_row = await client.get(f"/api/v1/llm-models/{user_id}")
            auto_row = await client.get(f"/api/v1/llm-models/{auto_id}")
            self.assertEqual(user_row.json()["input_modalities"], ["text"])
            self.assertEqual(user_row.json()["capabilities_source"], "user")
            self.assertEqual(auto_row.json()["capabilities_source"], "models_dev")

    async def test_refresh_metadata_for_test_env_bare_models(self) -> None:
        test_models = (
            {
                "name": "deepseek-v4-flash",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
                "input_modalities": ["text"],
            },
            {
                "name": "kimi-k2.6",
                "model": "kimi-k2.6",
                "base_url": "https://api.moonshot.cn",
                "input_modalities": ["text", "image", "video"],
            },
            {
                "name": "kimi-k2.7-code-highspeed",
                "model": "kimi-k2.7-code-highspeed",
                "base_url": "https://api.moonshot.cn",
                "input_modalities": ["text", "image", "video"],
            },
        )
        with patch(
            "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
            return_value=None,
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                for case in test_models:
                    with self.subTest(name=case["name"]):
                        create_resp = await client.post(
                            "/api/v1/llm-models",
                            json={
                                "name": case["name"],
                                "model": case["model"],
                                "base_url": case["base_url"],
                            },
                        )
                        self.assertEqual(create_resp.status_code, 201)
                        model_id = create_resp.json()["id"]

                        refresh_resp = await client.post(
                            f"/api/v1/llm-models/{model_id}/refresh-metadata"
                        )
                        self.assertEqual(refresh_resp.status_code, 200)
                        body = refresh_resp.json()
                        self.assertEqual(body["input_modalities"], case["input_modalities"])
                        self.assertEqual(body["capabilities_source"], "bundled")


if __name__ == "__main__":
    unittest.main()
