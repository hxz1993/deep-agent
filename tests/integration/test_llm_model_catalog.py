"""Integration tests for the database-only model catalog API."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent.models.catalog import normalize_models_dev_bundle
from deep_agent.agent.models.models_dev import ModelsDevBundle, ModelsDevFetchError
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_catalog import LABS, MODELS, PROVIDERS


class LlmModelCatalogIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, session_factory = await make_sqlite_stores()
        self._app = FastAPI()
        self._app.include_router(api_router)
        allow_console_admin(self._app)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
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

    async def _seed_catalog(self) -> None:
        rows, _stats = normalize_models_dev_bundle(MODELS, PROVIDERS, LABS)
        await self.stores.model_catalog.replace_all(rows)

    async def test_get_is_database_only_and_supports_both_views(self) -> None:
        await self._seed_catalog()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            model_response = await client.get(
                "/api/v1/llm-models/catalog",
                params={"view": "model", "lab_id": "anthropic", "modality": "image"},
            )
            provider_response = await client.get(
                "/api/v1/llm-models/catalog",
                params={"view": "provider", "provider_id": "openai"},
            )
        self.assertEqual(model_response.status_code, 200)
        self.assertEqual(model_response.json()["total"], 1)
        self.assertEqual(model_response.json()["items"][0]["kind"], "model")
        self.assertEqual(model_response.json()["items"][0]["provider_count"], 1)
        self.assertEqual(provider_response.status_code, 200)
        self.assertEqual(provider_response.json()["items"][0]["provider_id"], "openai")
        self.assertTrue(provider_response.json()["facets"]["labs"])

    async def test_refresh_replaces_catalog_after_complete_validation(self) -> None:
        await self._seed_catalog()
        bundle = ModelsDevBundle(models=MODELS, providers=PROVIDERS, labs=LABS)
        with patch(
            "deep_agent.api.v1.llm_models.fetch_models_dev_bundle",
            new=AsyncMock(return_value=bundle),
        ):
            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
            ) as client:
                response = await client.post("/api/v1/llm-models/catalog/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], 2)
        self.assertEqual(len(await self.stores.model_catalog.list_all()), 2)

    async def test_fetch_failure_preserves_old_catalog(self) -> None:
        await self._seed_catalog()
        before = [
            (row.provider_id, row.provider_model_id)
            for row in await self.stores.model_catalog.list_all()
        ]
        with patch(
            "deep_agent.api.v1.llm_models.fetch_models_dev_bundle",
            new=AsyncMock(side_effect=ModelsDevFetchError("upstream unavailable")),
        ):
            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
            ) as client:
                response = await client.post("/api/v1/llm-models/catalog/refresh")
        self.assertEqual(response.status_code, 502)
        after = [
            (row.provider_id, row.provider_model_id)
            for row in await self.stores.model_catalog.list_all()
        ]
        self.assertEqual(after, before)

    async def test_old_source_and_refresh_query_parameters_are_rejected(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            source_response = await client.get(
                "/api/v1/llm-models/catalog",
                params={"source": "bundled"},
            )
            refresh_response = await client.get(
                "/api/v1/llm-models/catalog",
                params={"refresh": "true"},
            )
        # FastAPI ignores unknown query parameters by design, but neither parameter
        # changes the empty database result or triggers an upstream request.
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(source_response.json()["total"], 0)
        self.assertEqual(refresh_response.json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
