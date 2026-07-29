"""LLM runtime facts CRUD and DB-only modality behavior."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent.attachments import model_supports_image_input
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores


def model_payload(**overrides) -> dict:
    payload = {
        "name": "private-model",
        "model": "private:my-model",
        "input_modalities": ["text"],
        "context_window_tokens": 128_000,
        "max_output_tokens": 8_192,
        "tags": ["tool_call"],
    }
    payload.update(overrides)
    return payload


class LlmModelRuntimeFactsIntegrationTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_crud_persists_required_runtime_facts(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(
                    input_modalities=["text", "image"],
                    context_window_tokens=200_000,
                    max_output_tokens=16_384,
                    tags=["reasoning", "tool_call"],
                ),
            )
            self.assertEqual(create_response.status_code, 201)
            created = create_response.json()
            self.assertEqual(created["input_modalities"], ["text", "image"])
            self.assertEqual(created["context_window_tokens"], 200_000)
            self.assertEqual(created["max_output_tokens"], 16_384)
            self.assertEqual(created["tags"], ["reasoning", "tool_call"])
            self.assertNotIn("capabilities_source", created)

            patch_response = await client.patch(
                f"/api/v1/llm-models/{created['id']}",
                json={"input_modalities": ["text"], "tags": []},
            )
            self.assertEqual(patch_response.status_code, 200)
            patched = patch_response.json()
            self.assertFalse(
                model_supports_image_input(
                    db_modalities=patched["input_modalities"],
                )
            )

    async def test_create_requires_modalities_context_and_output(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models",
                json={"name": "missing-facts", "model": "private:model"},
            )
        self.assertEqual(response.status_code, 422)
        error_fields = {item["loc"][-1] for item in response.json()["detail"]}
        self.assertEqual(
            error_fields,
            {"input_modalities", "context_window_tokens", "max_output_tokens"},
        )

    async def test_max_output_cannot_exceed_context(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(
                    context_window_tokens=4_096,
                    max_output_tokens=8_192,
                ),
            )
        self.assertEqual(response.status_code, 422)

    async def test_legacy_metadata_refresh_endpoints_are_removed(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(),
            )
            model_id = create_response.json()["id"]
            one = await client.post(f"/api/v1/llm-models/{model_id}/refresh-metadata")
            batch = await client.post("/api/v1/llm-models/refresh-metadata")
        self.assertIn(one.status_code, {404, 405})
        self.assertIn(batch.status_code, {404, 405, 422})


if __name__ == "__main__":
    unittest.main()
