from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

import aiohttp
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.admin import require_console_admin
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.security import (
    ConsoleAdminAuthenticationError,
    ConsoleAdminAuthorizer,
    ConsoleAdminUnavailableError,
    PlatformEnterpriseAdminChecker,
)
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text
        self.released = False

    async def text(self) -> str:
        return self._text

    def release(self) -> None:
        self.released = True


def _request(path: str = "/api/v1/admin/access", **headers: str):
    from starlette.requests import Request

    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in headers.items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": raw_headers,
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 123),
            "scheme": "http",
        }
    )


class ConsoleAdminAuthorizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enterprise_admin_is_allowed_and_sensitive_headers_are_not_cached(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        async def checker(user_id: str, headers: Mapping[str, str]) -> bool:
            calls.append((user_id, dict(headers)))
            return True

        authorizer = ConsoleAdminAuthorizer(checker)
        request = _request(
            user="enterprise-user",
            authorization="Bearer very-secret-token",
            cookie="session=very-secret-cookie",
            app="should-not-forward",
            space="should-not-forward",
        )
        first = await authorizer.access_for(request, "enterprise-user")
        second = await authorizer.access_for(request, "enterprise-user")

        self.assertTrue(first.allowed)
        self.assertEqual(first.roles, ("enterprise_admin",))
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "enterprise-user")
        self.assertEqual(calls[0][1]["authorization"], "Bearer very-secret-token")
        self.assertNotIn("app", calls[0][1])
        self.assertNotIn("space", calls[0][1])
        self.assertNotIn("very-secret-token", repr(authorizer._cache))
        self.assertNotIn("very-secret-cookie", repr(authorizer._cache))

    async def test_normal_user_is_denied_and_false_decision_is_cached(self) -> None:
        checker = AsyncMock(return_value=False)
        authorizer = ConsoleAdminAuthorizer(checker)
        request = _request(user="normal-user", authorization="Bearer token")

        first = await authorizer.access_for(request, "normal-user")
        second = await authorizer.access_for(request, "normal-user")

        self.assertFalse(first.allowed)
        self.assertEqual(first.roles, ())
        self.assertEqual(second, first)
        checker.assert_awaited_once()

    async def test_fixed_admin_is_denied_without_calling_platform(self) -> None:
        checker = AsyncMock(return_value=True)
        authorizer = ConsoleAdminAuthorizer(checker)

        access = await authorizer.access_for(_request(user="admin"), "admin")

        self.assertFalse(access.allowed)
        checker.assert_not_awaited()

    async def test_cache_expires_and_credential_change_uses_a_new_key(self) -> None:
        now = [100.0]
        checker = AsyncMock(return_value=True)
        authorizer = ConsoleAdminAuthorizer(
            checker,
            cache_ttl_seconds=30,
            clock=lambda: now[0],
        )

        await authorizer.access_for(
            _request(user="u1", authorization="Bearer token-a"),
            "u1",
        )
        await authorizer.access_for(
            _request(user="u1", authorization="Bearer token-b"),
            "u1",
        )
        now[0] = 131.0
        await authorizer.access_for(
            _request(user="u1", authorization="Bearer token-a"),
            "u1",
        )

        self.assertEqual(checker.await_count, 3)

    async def test_failures_are_not_cached(self) -> None:
        checker = AsyncMock(
            side_effect=[ConsoleAdminUnavailableError("down"), True]
        )
        authorizer = ConsoleAdminAuthorizer(checker)
        request = _request(user="u1", authorization="Bearer token")

        with self.assertRaises(ConsoleAdminUnavailableError):
            await authorizer.access_for(request, "u1")
        access = await authorizer.access_for(request, "u1")

        self.assertTrue(access.allowed)
        self.assertEqual(checker.await_count, 2)

class PlatformEnterpriseAdminCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_boolean_and_platform_envelope_responses(self) -> None:
        response = _FakeResponse(
            200,
            '{"status":true,"data":true,"code":0,"message":null}',
        )
        with patch(
            "deep_agent.security.console_admin.AioHttpCli.get",
            new=AsyncMock(return_value=response),
        ):
            allowed = await PlatformEnterpriseAdminChecker(
                base_url="https://gateway.test/deepfos-server/"
            )("u1", {"authorization": "Bearer token"})

        self.assertTrue(allowed)
        self.assertTrue(response.released)

        direct_response = _FakeResponse(200, "false")
        with patch(
            "deep_agent.security.console_admin.AioHttpCli.get",
            new=AsyncMock(return_value=direct_response),
        ):
            denied = await PlatformEnterpriseAdminChecker(
                base_url="https://gateway.test/deepfos-server/"
            )("u1", {})
        self.assertFalse(denied)

    async def test_401_is_authentication_error(self) -> None:
        response = _FakeResponse(401, "unauthorized")
        with patch(
            "deep_agent.security.console_admin.AioHttpCli.get",
            new=AsyncMock(return_value=response),
        ):
            with self.assertRaises(ConsoleAdminAuthenticationError):
                await PlatformEnterpriseAdminChecker(
                    base_url="https://gateway.test/deepfos-server/"
                )("u1", {})

    async def test_timeout_and_malformed_data_are_unavailable(self) -> None:
        checker = PlatformEnterpriseAdminChecker(
            base_url="https://gateway.test/deepfos-server/"
        )
        with patch(
            "deep_agent.security.console_admin.AioHttpCli.get",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            with self.assertRaises(ConsoleAdminUnavailableError):
                await checker("u1", {})

        for body in (
            '{"allowed": true}',
            '{"status": false, "data": true}',
            '{"status": true, "data": "true"}',
        ):
            with self.subTest(body=body), patch(
                "deep_agent.security.console_admin.AioHttpCli.get",
                new=AsyncMock(return_value=_FakeResponse(200, body)),
            ):
                with self.assertRaises(ConsoleAdminUnavailableError):
                    await checker("u1", {})


class ConsoleAdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stores, self.engine, session_factory = make_sqlite_stores_sync()
        self.checker = AsyncMock(return_value=True)
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
                console_admin_authorizer=ConsoleAdminAuthorizer(self.checker),
            )
        )
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(
            app,
            headers={"user": "enterprise-user", "authorization": "Bearer token"},
        )

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_access_endpoint_and_management_route_allow_enterprise_admin(self) -> None:
        access = self.client.get("/api/v1/admin/access")
        agents = self.client.get("/api/v1/agents")

        self.assertEqual(access.status_code, 200, access.text)
        self.assertEqual(
            access.json(),
            {"allowed": True, "roles": ["enterprise_admin"]},
        )
        self.assertEqual(agents.status_code, 200, agents.text)

    def test_normal_user_and_fixed_admin_are_denied(self) -> None:
        self.checker.return_value = False
        normal = self.client.get(
            "/api/v1/agents",
            headers={"user": "normal-user", "authorization": "Bearer normal"},
        )
        fixed_admin_access = self.client.get(
            "/api/v1/admin/access",
            headers={"user": "admin", "authorization": "Bearer admin"},
        )
        fixed_admin_agents = self.client.get(
            "/api/v1/agents",
            headers={"user": "admin", "authorization": "Bearer admin"},
        )

        self.assertEqual(normal.status_code, 403)
        self.assertEqual(
            fixed_admin_access.json(),
            {"allowed": False, "roles": []},
        )
        self.assertEqual(fixed_admin_agents.status_code, 403)

    def test_missing_identity_auth_failure_and_platform_failure(self) -> None:
        missing = self.client.get(
            "/api/v1/admin/access",
            headers={"user": "", "authorization": ""},
        )
        self.assertEqual(missing.status_code, 401)

        for error, expected_status in (
            (ConsoleAdminAuthenticationError("expired"), 401),
            (ConsoleAdminUnavailableError("down"), 503),
        ):
            with self.subTest(error=type(error).__name__):
                authorizer = ConsoleAdminAuthorizer(AsyncMock(side_effect=error))
                from deep_agent.core.context import get_context

                get_context().console_admin_authorizer = authorizer
                response = self.client.get(
                    "/api/v1/admin/access",
                    headers={"user": "other-user", "authorization": "Bearer other"},
                )
                self.assertEqual(response.status_code, expected_status, response.text)

    def test_runtime_routes_do_not_require_console_admin(self) -> None:
        self.checker.return_value = False

        detail = self.client.get(
            "/api/v1/agents/00000000-0000-0000-0000-000000000000",
            headers={"user": "normal-user", "authorization": "Bearer normal"},
        )
        uploads = self.client.get(
            "/api/v1/uploads/capabilities",
            headers={"user": "normal-user", "authorization": "Bearer normal"},
        )
        request_context = self.client.get(
            "/api/v1/request-context",
            headers={"user": "normal-user", "authorization": "Bearer normal"},
        )

        self.assertEqual(detail.status_code, 404, detail.text)
        self.assertEqual(uploads.status_code, 200, uploads.text)
        self.assertEqual(request_context.status_code, 200, request_context.text)


class ConsoleAdminRouteAuditTests(unittest.TestCase):
    def test_management_routes_have_the_unified_dependency(self) -> None:
        protected_prefixes = (
            "/api/v1/auxiliary-models",
            "/api/v1/context-test-scenarios",
            "/api/v1/llm-models",
            "/api/v1/mcp",
            "/api/v1/skills",
            "/api/v1/ui-tool-packages",
            "/api/v1/ui-tools",
        )
        protected_exact = {
            "/api/v1/agents",
            "/api/v1/agents/prompt-estimate",
            "/api/v1/database-info",
            "/api/v1/global-settings",
            "/api/v1/health/sandbox",
            "/api/v1/system-tools",
            "/api/v1/tools",
        }
        app = FastAPI()
        app.include_router(api_router)
        audited = 0

        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            should_be_protected = route.path in protected_exact or route.path.startswith(
                protected_prefixes
            )
            if route.path.startswith("/api/v1/agents/") and route.path != "/api/v1/agents/prompt-estimate":
                should_be_protected = route.methods != {"GET"}
            if not should_be_protected:
                continue
            audited += 1
            dependencies = {dependency.call for dependency in route.dependant.dependencies}
            self.assertIn(require_console_admin, dependencies, f"unprotected route: {route.path}")

        self.assertGreaterEqual(audited, 30)


if __name__ == "__main__":
    unittest.main()
