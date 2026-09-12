"""Real SQLite/API checks for consuming configuration and the shared API help."""
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.sqlite_stores import make_sqlite_stores


class IntegrationRuntimeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, factory = await make_sqlite_stores()
        self.checker = AsyncMock(return_value=True)
        init_context(AppContext.for_testing(stores=self.stores, db_engine=self.engine,
                     db_session_factory=factory, console_admin_authorizer=ConsoleAdminAuthorizer(self.checker)))
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.headers = {"user": "enterprise-user", "authorization": "Bearer test-only"}

    async def asyncTearDown(self):
        await self.client.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_reads_never_seed_and_unconfigured_is_explicit(self):
        for code in ("deeptable-assistant", "deepcube-assistant"):
            response = await self.client.get(f"/api/v1/integrations/{code}", headers=self.headers)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "integration_not_configured")
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(await self.stores.integration_settings.list_all(), [])
        unknown = await self.client.get("/api/v1/integrations/missing", headers=self.headers)
        self.assertEqual(unknown.status_code, 404)

    async def test_json_roundtrip_ordinary_user_read_and_admin_only_mutations(self):
        path = "/api/v1/integrations/deeptable-assistant"
        settings = {"agent_id": "example-agent", "options": {"enabled": True, "count": 2, "values": [None, "中文", 1.25]}}
        saved = await self.client.put(path, json={"settings": settings}, headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.checker.return_value = False
        user = {"user": "normal-user", "authorization": "Bearer different-test-only"}
        read = await self.client.get(path, headers=user)
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), {"code": "deeptable-assistant", "settings": settings})
        self.assertEqual(read.headers["cache-control"], "no-store")
        self.assertEqual((await self.client.get(path)).status_code, 401)
        self.assertEqual((await self.client.put(path, json={"settings": {}}, headers=user)).status_code, 403)
        self.assertEqual((await self.client.delete(path, headers=user)).status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/integrations", headers=user)).status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/integrations/deeptable-assistant/api", headers=user)).status_code, 403)
        self.assertEqual((await self.client.get(path, headers=user)).json()["settings"], settings)
        cleared = await self.client.delete(path, headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual((await self.client.get(path, headers=user)).status_code, 409)

    async def test_only_settings_field_is_accepted_and_documented(self):
        path = "/api/v1/integrations/deeptable-assistant"
        rejected = await self.client.put(path, json={"config": {"agent_id": "old-field"}}, headers=self.headers)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(await self.stores.integration_settings.list_all(), [])
        schemas = self.app.openapi()["components"]["schemas"]
        write_schema = schemas["IntegrationSettingsWrite"]
        self.assertEqual(set(write_schema["properties"]), {"settings"})
        self.assertEqual(write_schema["required"], ["settings"])
        self.assertEqual(set(schemas["IntegrationSettingsResponse"]["properties"]), {"code", "settings"})

    async def test_resource_routes_and_manifest_do_not_conflict(self):
        schema = self.app.openapi()
        path = "/api/v1/integrations/{code}"
        self.assertEqual(set(schema["paths"][path]), {"get", "put", "delete"})
        self.assertEqual(set(schema["paths"]["/api/v1/integrations"]), {"get"})
        self.assertEqual(set(schema["paths"]["/api/v1/integrations/{code}/api"]), {"get"})
        self.assertNotIn(path + "/configuration", schema["paths"])
        for method in ("GET", "PUT", "DELETE"):
            response = await self.client.request(method, "/api/v1/integrations/deeptable-assistant/configuration", headers=self.headers)
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(await self.stores.integration_settings.list_all(), [])

    async def test_popup_and_openapi_use_same_endpoint_and_examples(self):
        schema = self.app.openapi()
        documented = schema["paths"]["/api/v1/integrations/{code}"]["get"]
        self.assertEqual(set(documented["responses"]), {"200", "401", "404", "409", "422"})
        for code in ("deeptable-assistant", "deepcube-assistant"):
            response = await self.client.get(f"/api/v1/integrations/{code}/api", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            help = response.json()
            self.assertEqual(help["path"], f"/api/v1/integrations/{code}")
            self.assertEqual(help["method"], "GET")
            self.assertEqual(help["description"], documented["description"])
            self.assertEqual(help["response_example"], documented["responses"]["200"]["content"]["application/json"]["examples"][code]["value"])
            self.assertEqual(help["parameters"][0]["example"], code)
        self.assertEqual(await self.stores.integration_settings.list_all(), [])

    async def test_invalid_configuration_is_not_cached_or_echoed(self):
        response = await self.client.put("/api/v1/integrations/deeptable-assistant",
            json={"settings": ["private-input-not-for-errors"]}, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("private-input-not-for-errors", response.text)
        self.assertEqual(await self.stores.integration_settings.list_all(), [])
        for number in ("NaN", "Infinity", "-Infinity"):
            response = await self.client.put("/api/v1/integrations/deeptable-assistant",
                content='{"settings":{"value":' + number + '}}',
                headers={**self.headers, "content-type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
