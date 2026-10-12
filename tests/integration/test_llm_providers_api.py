from __future__ import annotations

import unittest
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import LlmModel
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class LlmProviderApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, self._session_factory = await make_sqlite_stores()
        async with self._engine.begin() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
        app = FastAPI()
        app.include_router(api_router)
        allow_console_admin(app)
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self._engine,
                db_session_factory=self._session_factory,
            )
        )
        self._client = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )
        self._default_model = (await self.stores.llm_model.list_all())[0]

    async def asyncTearDown(self) -> None:
        await self._client.aclose()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_type_catalog_uses_the_public_contract_and_registry_order(self) -> None:
        response = await self._client.get("/api/v1/llm-provider-types")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual(items[0]["code"], "deepseek")
        self.assertEqual(items[-2]["code"], "openai_compatible")
        self.assertEqual(items[-1]["code"], "openai_codex")
        self.assertEqual(
            set(items[0]),
            {
                "code",
                "label",
                "order",
                "default_base_url",
                "supports_default_base_url",
                "supports_custom_base_url",
                "model_protocol",
            },
        )
        self.assertFalse(items[-2]["supports_default_base_url"])
        self.assertIsNone(items[-2]["default_base_url"])

    async def test_provider_key_is_masked_preserved_and_explicitly_cleared(self) -> None:
        created = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "DeepSeek Test",
                "provider_type": "deepseek",
                "use_default_base_url": True,
                "api_key": "top-secret",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertNotIn("api_key", body)
        self.assertTrue(body["api_key_set"])
        self.assertEqual(body["api_key_masked"], "top********ret")
        self.assertNotIn("top-secret", created.text)
        self.assertEqual(body["base_url"], "https://api.deepseek.com")

        preserved = await self._client.patch(
            f"/api/v1/llm-providers/{body['id']}", json={"name": "DeepSeek Renamed"}
        )
        self.assertTrue(preserved.json()["api_key_set"])
        model = await self._client.post(
            "/api/v1/llm-models",
            json={
                "provider_id": body["id"],
                "name": "key-clear-model",
                "model": "key-clear-model",
                "input_modalities": ["text"],
                "context_window_tokens": 32_000,
                "max_output_tokens": 4_096,
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        self.assertTrue(model.json()["enabled"])

        cleared = await self._client.patch(
            f"/api/v1/llm-providers/{body['id']}",
            json={"clear_api_key": True},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["api_key_set"])
        self.assertIsNone(cleared.json()["api_key_masked"])
        unavailable_model = await self._client.get(
            f"/api/v1/llm-models/{model.json()['id']}"
        )
        self.assertEqual(unavailable_model.status_code, 200, unavailable_model.text)
        self.assertFalse(unavailable_model.json()["selectable"])
        self.assertEqual(
            unavailable_model.json()["unavailable_reason"],
            "provider_unconfigured",
        )

        restored = await self._client.patch(
            f"/api/v1/llm-providers/{body['id']}",
            json={"api_key": "replacement-secret"},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        selectable_model = await self._client.get(
            f"/api/v1/llm-models/{model.json()['id']}"
        )
        self.assertTrue(selectable_model.json()["selectable"])

    async def test_ruge_create_succeeds_once_and_real_duplicate_is_a_safe_conflict(self) -> None:
        first_secret = "ruge-first-secret"
        created = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "ruge",
                "provider_type": "openai_compatible",
                "use_default_base_url": False,
                "base_url": "https://ruge.example/v1",
                "api_key": first_secret,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["name"], "ruge")
        self.assertTrue(created.json()["api_key_set"])
        self.assertNotIn(first_secret, created.text)

        listed = await self._client.get("/api/v1/llm-providers")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [item["name"] for item in listed.json() if item["name"] == "ruge"],
            ["ruge"],
        )
        self.assertNotIn(first_secret, listed.text)

        duplicate_secret = "ruge-duplicate-secret"
        duplicate = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "ruge",
                "provider_type": "openai_compatible",
                "use_default_base_url": False,
                "base_url": "https://duplicate.example/v1",
                "api_key": duplicate_secret,
            },
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertIn("already exists: ruge", duplicate.text)
        self.assertNotIn(first_secret, duplicate.text)
        self.assertNotIn(duplicate_secret, duplicate.text)

    async def test_provider_and_model_configuration_can_be_completed_independently(self) -> None:
        created = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "Unconfigured Provider",
                "provider_type": "deepseek",
                "use_default_base_url": True,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertFalse(body["api_key_set"])

        pending_model = await self._client.post(
            "/api/v1/llm-models",
            json={
                "provider_id": body["id"],
                "name": "unconfigured-model",
                "model": "unconfigured-model",
                "input_modalities": ["text"],
                "context_window_tokens": 32_000,
                "max_output_tokens": 4_096,
            },
        )
        self.assertEqual(pending_model.status_code, 201, pending_model.text)
        self.assertFalse(pending_model.json()["selectable"])
        self.assertEqual(
            pending_model.json()["unavailable_reason"],
            "provider_unconfigured",
        )

        configured = await self._client.patch(
            f"/api/v1/llm-providers/{body['id']}",
            json={"api_key": "configured-secret"},
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        restored_pending = await self._client.get(
            f"/api/v1/llm-models/{pending_model.json()['id']}"
        )
        self.assertTrue(restored_pending.json()["selectable"])

    async def test_provider_enabled_is_rejected_but_model_enabled_controls_selection(self) -> None:
        provider_id = self._default_model.provider_id
        provider_flag = await self._client.patch(
            f"/api/v1/llm-providers/{provider_id}",
            json={"enabled": False},
        )
        self.assertEqual(provider_flag.status_code, 422, provider_flag.text)

        existing_agent = await self._client.post(
            "/api/v1/agents",
            json={
                "name": "existing-model-agent",
                "model_id": self._default_model.id,
                "prompt": "existing references must remain intact",
            },
        )
        self.assertEqual(existing_agent.status_code, 201, existing_agent.text)

        model_flag = await self._client.patch(
            f"/api/v1/llm-models/{self._default_model.id}",
            json={"enabled": False},
        )
        self.assertEqual(model_flag.status_code, 200, model_flag.text)
        self.assertFalse(model_flag.json()["enabled"])
        self.assertFalse(model_flag.json()["selectable"])
        self.assertEqual(model_flag.json()["unavailable_reason"], "model_disabled")

        ordinary = await self._client.get("/api/v1/llm-models")
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        self.assertNotIn(self._default_model.id, {item["id"] for item in ordinary.json()})
        administrative = await self._client.get(
            "/api/v1/llm-models", params={"selectable_only": False}
        )
        self.assertIn(
            self._default_model.id,
            {item["id"] for item in administrative.json()},
        )

        rejected_agent = await self._client.post(
            "/api/v1/agents",
            json={
                "name": "disabled-model-agent",
                "model_id": self._default_model.id,
                "prompt": "disabled models cannot be newly selected",
            },
        )
        self.assertEqual(rejected_agent.status_code, 422, rejected_agent.text)

        retained_agent = await self._client.patch(
            f"/api/v1/agents/{existing_agent.json()['id']}",
            json={"prompt": "the reference remains while disabled"},
        )
        self.assertEqual(retained_agent.status_code, 200, retained_agent.text)
        self.assertEqual(retained_agent.json()["model_id"], self._default_model.id)

        referenced_delete = await self._client.delete(
            f"/api/v1/llm-models/{self._default_model.id}"
        )
        self.assertEqual(referenced_delete.status_code, 409, referenced_delete.text)

        reenabled = await self._client.patch(
            f"/api/v1/llm-models/{self._default_model.id}",
            json={"enabled": True},
        )
        self.assertEqual(reenabled.status_code, 200, reenabled.text)
        self.assertTrue(reenabled.json()["selectable"])

        created_agent = await self._client.post(
            "/api/v1/agents",
            json={
                "name": "configured-model-agent",
                "model_id": self._default_model.id,
                "prompt": "model association selects this model",
            },
        )
        self.assertEqual(created_agent.status_code, 201, created_agent.text)

    async def test_compact_provider_id_lifecycle_and_model_filter(self) -> None:
        created = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "Lifecycle Provider",
                "provider_type": "openai_compatible",
                "use_default_base_url": False,
                "base_url": "https://lifecycle.invalid/v1/",
                "api_key": "lifecycle-secret",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        provider_id = created.json()["id"]
        self.assertEqual(len(provider_id), 32)
        self.assertEqual(provider_id, UUID(provider_id).hex)

        hyphenated_id = str(UUID(provider_id))
        fetched = await self._client.get(f"/api/v1/llm-providers/{hyphenated_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["id"], provider_id)

        updated = await self._client.patch(
            f"/api/v1/llm-providers/{hyphenated_id}",
            json={"name": "Lifecycle Provider Renamed"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "Lifecycle Provider Renamed")

        model = await self._client.post(
            "/api/v1/llm-models",
            json={
                "provider_id": hyphenated_id,
                "name": "lifecycle-model",
                "model": "lifecycle-model",
                "input_modalities": ["text"],
                "context_window_tokens": 32_000,
                "max_output_tokens": 4_096,
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        self.assertEqual(model.json()["provider_id"], provider_id)

        filtered = await self._client.get(
            "/api/v1/llm-models",
            params={"provider_id": hyphenated_id},
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual([item["id"] for item in filtered.json()], [model.json()["id"]])

        deleted_provider = await self._client.delete(
            f"/api/v1/llm-providers/{hyphenated_id}"
        )
        self.assertEqual(deleted_provider.status_code, 200, deleted_provider.text)
        missing = await self._client.get(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertIsNone(await self.stores.llm_model.get_by_id(model.json()["id"]))
        self.assertIsNotNone(await self.stores.llm_model.get_by_id(self._default_model.id))

    async def test_compatible_requires_custom_url_and_effective_availability_is_derived(self) -> None:
        invalid = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "Invalid Compatible",
                "provider_type": "openai_compatible",
                "use_default_base_url": True,
                "api_key": "secret",
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)

        missing_key = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "Missing Key",
                "provider_type": "openai_compatible",
                "base_url": "https://missing-key.invalid/v1",
            },
        )
        self.assertEqual(missing_key.status_code, 201, missing_key.text)
        self.assertFalse(missing_key.json()["api_key_set"])

        keyed_provider = await self._client.post(
            "/api/v1/llm-providers",
            json={
                "name": "Keyed Compatible",
                "provider_type": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "api_key": "provider-secret",
            },
        )
        self.assertEqual(keyed_provider.status_code, 201, keyed_provider.text)
        model = await self._client.post(
            "/api/v1/llm-models",
            json={
                "provider_id": keyed_provider.json()["id"],
                "name": "configured-provider-model",
                "model": "configured-provider-model",
                "input_modalities": ["text"],
                "context_window_tokens": 32_000,
                "max_output_tokens": 4_096,
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        self.assertTrue(model.json()["selectable"])

        enabled_without_key = await self._client.post(
            "/api/v1/llm-models",
            json={
                "provider_id": missing_key.json()["id"],
                "name": "model-without-key",
                "model": "model-without-key",
                "input_modalities": ["text"],
                "context_window_tokens": 32_000,
                "max_output_tokens": 4_096,
            },
        )
        self.assertEqual(enabled_without_key.status_code, 201, enabled_without_key.text)
        self.assertFalse(enabled_without_key.json()["selectable"])
        switched = await self._client.patch(
            f"/api/v1/llm-models/{self._default_model.id}",
            json={"provider_id": missing_key.json()["id"]},
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertFalse(switched.json()["selectable"])
        self.assertEqual(switched.json()["unavailable_reason"], "provider_unconfigured")

    async def test_model_responses_are_provider_derived_and_provider_filter_is_supported(self) -> None:
        provider = await self.stores.llm_configuration.get_provider(
            self._default_model.provider_id
        )
        assert provider is not None
        self.assertNotIn("base_url", LlmModel.__table__.c)
        self.assertNotIn("api_key", LlmModel.__table__.c)
        updated_provider = await self._client.patch(
            f"/api/v1/llm-providers/{provider.id}",
            json={"base_url": "https://updated.invalid/v1"},
        )
        self.assertEqual(updated_provider.status_code, 200, updated_provider.text)

        response = await self._client.get(
            "/api/v1/llm-models", params={"provider_id": provider.id}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
        model = response.json()[0]
        self.assertEqual(model["provider"]["id"], provider.id)
        self.assertTrue(model["provider"]["api_key_set"])
        self.assertEqual(model["base_url"], "https://updated.invalid/v1")
        self.assertTrue(model["api_key_set"])
        self.assertTrue(model["enabled"])
        self.assertTrue(model["selectable"])

    async def test_last_provider_with_models_can_be_deleted_but_cannot_change_type(self) -> None:
        provider_id = self._default_model.provider_id
        changed = await self._client.patch(
            f"/api/v1/llm-providers/{provider_id}",
            json={"provider_type": "openai", "use_default_base_url": True},
        )
        self.assertEqual(changed.status_code, 409, changed.text)
        deleted = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual((await self._client.get("/api/v1/llm-providers")).json(), [])
        self.assertEqual((await self._client.get("/api/v1/llm-models")).json(), [])
        again = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(again.status_code, 404, again.text)
        created = await self._client.post(
            "/api/v1/llm-providers",
            json={"name": "New empty provider", "provider_type": "deepseek", "use_default_base_url": True},
        )
        self.assertEqual(created.status_code, 201, created.text)
        deleted_empty = await self._client.delete(f"/api/v1/llm-providers/{created.json()['id']}")
        self.assertEqual(deleted_empty.status_code, 200, deleted_empty.text)

    async def test_agent_reference_rejects_entire_provider_delete_with_agent_name(self) -> None:
        agent_id = await seed_default_agent(self.stores, name="预算分析助手")
        provider_id = self._default_model.provider_id
        unused_model = await self._client.post(
            "/api/v1/llm-models",
            json={"provider_id": provider_id, "name": "unused", "model": "unused", "input_modalities": ["text"], "context_window_tokens": 32000, "max_output_tokens": 4096},
        )
        self.assertEqual(unused_model.status_code, 201, unused_model.text)
        deleted = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(deleted.status_code, 409, deleted.text)
        self.assertIn("预算分析助手", deleted.json()["detail"])
        self.assertIsNotNone(await self.stores.llm_configuration.get_provider(provider_id))
        self.assertEqual(len(await self.stores.llm_model.list_all()), 2)
        self.assertIsNotNone(await self.stores.agent.get_by_id(agent_id))

    async def test_active_search_credential_reference_must_be_changed_before_delete(self) -> None:
        provider_id = self._default_model.provider_id
        await self.stores.global_setting.upsert("web_search", version=1, value={
            "mode": "provider", "provider": "deepseek",
            "credential_source": "llm_provider", "llm_provider_id": provider_id,
        })
        deleted = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(deleted.status_code, 409, deleted.text)
        self.assertIn("Web Search", deleted.json()["detail"])
        self.assertIsNotNone(await self.stores.llm_model.get_by_id(self._default_model.id))
        # An unused identifier in native search settings is not an active reference.
        await self.stores.global_setting.upsert("web_search", version=1, value={"mode": "native", "llm_provider_id": provider_id})
        deleted = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

    async def test_provider_delete_constraint_failure_rolls_back_model_deletion(self) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(text("""
                CREATE TRIGGER reject_provider_delete BEFORE DELETE ON llm_providers
                BEGIN SELECT RAISE(ABORT, 'FOREIGN KEY constraint failed'); END
            """))
        provider_id = self._default_model.provider_id
        deleted = await self._client.delete(f"/api/v1/llm-providers/{provider_id}")
        self.assertEqual(deleted.status_code, 409, deleted.text)
        self.assertNotIn("FOREIGN KEY", deleted.text)
        self.assertNotIn("DELETE FROM", deleted.text)
        self.assertIsNotNone(await self.stores.llm_configuration.get_provider(provider_id))
        self.assertIsNotNone(await self.stores.llm_model.get_by_id(self._default_model.id))


if __name__ == "__main__":
    unittest.main()
