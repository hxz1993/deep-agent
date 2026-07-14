"""Integration tests for GET /api/v1/llm-models/catalog."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent.models.catalog import clear_model_catalog_cache
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_catalog import SAMPLE_CATALOG


class LlmModelCatalogIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
        clear_model_catalog_cache()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_catalog_lists_bundled_entries(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/llm-models/catalog", params={"source": "bundled", "limit": 5})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreater(body["total"], 0)
        self.assertLessEqual(len(body["items"]), 5)
        self.assertEqual(body["source"], "bundled")
        self.assertTrue(body["providers"])

    async def test_catalog_filters_by_modality_and_paginates(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=SAMPLE_CATALOG),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/llm-models/catalog",
                    params={"source": "online", "modality": "pdf", "limit": 10},
                )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["model_id"], "gpt-4o")
        self.assertIn("pdf", body["items"][0]["input_modalities"])

    async def test_catalog_marks_configured_preset_by_full_key(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=SAMPLE_CATALOG),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                create_resp = await client.post(
                    "/api/v1/llm-models",
                    json={"name": "catalog-gpt", "model": "openai:gpt-4o"},
                )
                self.assertEqual(create_resp.status_code, 201)

                resp = await client.get(
                    "/api/v1/llm-models/catalog",
                    params={"source": "online", "provider": "openai", "q": "gpt-4o"},
                )
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        gpt4o = next(row for row in items if row["model_id"] == "gpt-4o")
        self.assertEqual(gpt4o["configured_preset"], "catalog-gpt")

    async def test_catalog_online_fallback_to_bundled(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/llm-models/catalog", params={"source": "online"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "bundled")
        self.assertIn("回退", body["source_label"])


if __name__ == "__main__":
    unittest.main()
