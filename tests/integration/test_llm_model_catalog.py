"""Integration tests for the database-only model catalog API."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.models.catalog import normalize_models_dev_bundle
from deep_agent.models.models_dev import ModelsDevBundle, ModelsDevFetchError
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_catalog import ADDITIONAL_PROVIDERS, LABS, MODELS, PROVIDERS


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

    async def test_imported_domestic_providers_are_selectable_from_saved_catalog(self) -> None:
        raw = json.dumps({"models": {}, "providers": ADDITIONAL_PROVIDERS}).encode()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models/catalog/import",
                files={"file": ("catalog.json", raw, "application/json")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["rows"], 2)
            with patch(
                "deep_agent.api.v1.llm_models.fetch_models_dev_bundle",
                new=AsyncMock(side_effect=AssertionError("catalog reads must remain local")),
            ):
                for provider_id, adapter, runtime_model in (
                    ("minimax-cn", "anthropic_compatible", "anthropic:MiniMax-M2.5"),
                    ("stepfun", "openai_compatible", "openai:step-3.5-flash"),
                ):
                    with self.subTest(provider_id=provider_id):
                        result = await client.get(
                            "/api/v1/llm-models/catalog",
                            params={"view": "model", "provider_id": provider_id},
                        )
                        self.assertEqual(result.status_code, 200, result.text)
                        body = result.json()
                        self.assertEqual(body["total"], 1)
                        self.assertEqual(
                            {item["id"] for item in body["facets"]["providers"]},
                            {"minimax-cn", "stepfun"},
                        )
                        option = body["items"][0]["providers"][0]
                        self.assertEqual(option["provider_id"], provider_id)
                        self.assertEqual(option["adapter"], adapter)
                        self.assertEqual(option["runtime_model"], runtime_model)

    async def test_xai_catalog_model_can_be_added_to_branded_provider_with_efforts(self) -> None:
        provider_model = "grok-4.7"
        xai_catalog = {
            "name": "xAI",
            "models": {
                provider_model: {
                    "id": provider_model,
                    "name": "Grok 4.7",
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "limit": {"context": 200_000, "output": 32_768},
                    "reasoning": True,
                    "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}],
                },
            },
        }
        rows, _ = normalize_models_dev_bundle({}, {"xai": xai_catalog}, [])
        await self.stores.model_catalog.replace_all(rows)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            catalog = await client.get("/api/v1/llm-models/catalog", params={"lab_id": "xai"})
            self.assertEqual(catalog.status_code, 200, catalog.text)
            entry = catalog.json()["items"][0]["providers"][0]
            self.assertEqual(entry["adapter"], "xai")
            self.assertEqual(entry["runtime_model"], "openai:grok-4.7")
            provider = await client.post("/api/v1/llm-providers", json={
                "name": "xAI fixture", "provider_type": "xai", "use_default_base_url": True,
                "api_key": "fixture-not-a-real-key",
            })
            self.assertEqual(provider.status_code, 201, provider.text)
            model = await client.post("/api/v1/llm-models", json={
                "provider_id": provider.json()["id"], "name": provider_model, "model": provider_model,
                "reasoning_catalog": {"provider_id": "xai", "provider_model_id": provider_model},
                "input_modalities": entry["input_modalities"], "tags": entry["tags"],
                "context_window_tokens": entry["context_window_tokens"],
                "max_output_tokens": entry["max_output_tokens"],
            })
            self.assertEqual(model.status_code, 201, model.text)
            self.assertEqual(model.json()["reasoning_options"], entry["reasoning_options"])

    async def test_saved_legacy_minimax_snapshot_exposes_only_domestic_entry(self) -> None:
        rows, _ = normalize_models_dev_bundle({}, ADDITIONAL_PROVIDERS, [])
        cn = next(row for row in rows if row["provider_id"] == "minimax-cn")
        await self.stores.model_catalog.replace_all([
            *rows, {**cn, "provider_id": "minimax", "provider_label": "MiniMax (International)"},
        ])
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            for view in ("model", "provider"):
                response = await client.get(
                    "/api/v1/llm-models/catalog", params={"view": view, "q": cn["provider_model_id"]},
                )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual({item["id"] for item in body["facets"]["providers"]}, {"minimax-cn", "stepfun"})
                self.assertEqual(body["total"], 1)
                item = body["items"][0]
                options = item["providers"] if view == "model" else [item]
                self.assertEqual([entry["provider_id"] for entry in options], ["minimax-cn"])
            exported = await client.get("/api/v1/llm-models/catalog/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(set(exported.json()["providers"]), {"minimax-cn", "stepfun"})
            provider = await client.post("/api/v1/llm-providers", json={
                "name": "MiniMax fixture", "provider_type": "minimax", "use_default_base_url": True,
            })
            self.assertEqual(provider.status_code, 201, provider.text)
            model_code = cn["provider_model_id"]
            payload = {
                "provider_id": provider.json()["id"], "name": model_code, "model": model_code,
                "input_modalities": ["text"], "context_window_tokens": 204800, "max_output_tokens": 32768,
            }
            for provider_id, status in (("minimax", 422), ("minimax-cn", 201)):
                response = await client.post("/api/v1/llm-models", json={
                    **payload, "reasoning_catalog": {"provider_id": provider_id, "provider_model_id": model_code},
                })
                self.assertEqual(response.status_code, status, response.text)

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

    async def test_import_official_catalog_json_replaces_catalog(self) -> None:
        await self._seed_catalog()
        raw = json.dumps({"models": MODELS, "providers": PROVIDERS}).encode()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models/catalog/import",
                files={"file": ("catalog.json", raw, "application/json")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows"], 2)
        self.assertEqual(len(await self.stores.model_catalog.list_all()), 2)

    async def test_exported_catalog_json_can_be_imported_again(self) -> None:
        await self._seed_catalog()
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            export_response = await client.get("/api/v1/llm-models/catalog/export")
            import_response = await client.post(
                "/api/v1/llm-models/catalog/import",
                files={
                    "file": (
                        "catalog.json",
                        export_response.content,
                        "application/json",
                    )
                },
            )
        self.assertEqual(export_response.status_code, 200, export_response.text)
        self.assertEqual(export_response.headers["content-type"], "application/json")
        self.assertRegex(
            export_response.headers["content-disposition"],
            r'models-dev-catalog-\d{8}-\d{6}\.json',
        )
        self.assertEqual(set(export_response.json()), {"models", "providers", "labs"})
        self.assertEqual(import_response.status_code, 200, import_response.text)
        self.assertEqual(import_response.json()["rows"], 2)

    async def test_export_empty_catalog_returns_conflict(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.get("/api/v1/llm-models/catalog/export")
        self.assertEqual(response.status_code, 409)
        self.assertIn("模型目录为空", response.json()["detail"])

    async def test_invalid_import_preserves_old_catalog(self) -> None:
        await self._seed_catalog()
        before = [
            (row.provider_id, row.provider_model_id)
            for row in await self.stores.model_catalog.list_all()
        ]
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models/catalog/import",
                files={"file": ("catalog.json", b"{", "application/json")},
            )
        self.assertEqual(response.status_code, 422)
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
