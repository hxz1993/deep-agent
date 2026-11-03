"""LLM runtime facts CRUD and DB-only modality behavior."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import get_default_test_provider_id, make_sqlite_stores


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
        self._provider_id = await get_default_test_provider_id(stores)
        self._app = FastAPI()
        self._app.include_router(api_router)
        allow_console_admin(self._app)
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
                    provider_id=self._provider_id,
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
            self.assertEqual(patched["input_modalities"], ["text"])

    async def test_compatibility_markers_round_trip_does_not_backfill_other_models(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test", headers={"user": "fixture-admin"}) as client:
            baseline = await client.post("/api/v1/llm-models", json=model_payload(
                provider_id=self._provider_id, name="unchanged", tags=[]))
            self.assertEqual(baseline.status_code, 201)
            before = baseline.json()
            before_read = await client.get(f"/api/v1/llm-models/{before['id']}")
            self.assertEqual(before_read.status_code, 200)
            tags = ["tool_call", "custom:existing", "single_system_message", "inline_tool_schema_refs"]
            enabled = await client.post("/api/v1/llm-models", json=model_payload(
                provider_id=self._provider_id, name="compatibility", tags=tags))
            self.assertEqual(enabled.status_code, 201)
            model_id = enabled.json()["id"]
            read = await client.get(f"/api/v1/llm-models/{model_id}")
            self.assertEqual(read.status_code, 200)
            self.assertEqual(read.json()["tags"], tags)
            edited = await client.patch(f"/api/v1/llm-models/{model_id}", json={"description": "edit"})
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["tags"], tags)
            disabled = await client.patch(f"/api/v1/llm-models/{model_id}", json={"tags": tags[:-1]})
            self.assertEqual(disabled.status_code, 200)
            self.assertEqual(disabled.json()["tags"], tags[:-1])
            unchanged = await client.get(f"/api/v1/llm-models/{before['id']}")
            self.assertEqual(unchanged.json(), before_read.json())
            stored = await get_context().stores.llm_model.get_by_id(before["id"])
            self.assertEqual(stored.updated_at.isoformat(), before["updated_at"].replace("Z", "+00:00"))

    async def test_create_requires_modalities_context_and_output(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/llm-models",
                json={
                    "provider_id": self._provider_id,
                    "name": "missing-facts",
                    "model": "private:model",
                },
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
                    provider_id=self._provider_id,
                    context_window_tokens=4_096,
                    max_output_tokens=8_192,
                ),
            )
        self.assertEqual(response.status_code, 422)

    async def test_legacy_metadata_refresh_endpoints_are_removed(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(provider_id=self._provider_id),
            )
            model_id = create_response.json()["id"]
            one = await client.post(f"/api/v1/llm-models/{model_id}/refresh-metadata")
            batch = await client.post("/api/v1/llm-models/refresh-metadata")
        self.assertIn(one.status_code, {404, 405})
        self.assertIn(batch.status_code, {404, 405, 422})

    async def test_agent_prompt_estimate_reuses_model_token_estimator(self) -> None:
        prompt = "分析 2026 年预算差异，并返回关键风险。"
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(
                    provider_id=self._provider_id,
                    name="estimate-model",
                ),
            )
            model = create_response.json()
            response = await client.post(
                "/api/v1/agents/prompt-estimate",
                json={"model_id": model["id"], "prompt": prompt},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        estimator = TokenEstimator.for_model("private:my-model")
        self.assertEqual(data["character_count"], len(prompt))
        self.assertEqual(data["estimated_tokens"], estimator.text(prompt))
        self.assertEqual(data["estimator_source"], estimator.source)

    async def test_agent_prompt_estimate_handles_empty_and_missing_model(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/llm-models",
                json=model_payload(
                    provider_id=self._provider_id,
                    name="empty-estimate-model",
                ),
            )
            model_id = create_response.json()["id"]
            empty = await client.post(
                "/api/v1/agents/prompt-estimate",
                json={"model_id": model_id, "prompt": "   "},
            )
            missing = await client.post(
                "/api/v1/agents/prompt-estimate",
                json={"model_id": "0" * 32, "prompt": "hello"},
            )

        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["character_count"], 0)
        self.assertEqual(empty.json()["estimated_tokens"], 0)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
