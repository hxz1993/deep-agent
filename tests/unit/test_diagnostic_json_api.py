from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import event

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.api.v1 import diagnostic_channels, diagnostic_sessions, diagnostic_usage, diagnostics
from deep_agent.api.v1.admin import require_console_admin, resolve_console_admin_access
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.core.context import get_app_context
from deep_agent.db.models import ChannelAccount
from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter
from deep_agent.security import ConsoleAdminAccess
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class DiagnosticJsonApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        await self.stores.coordinator.append("session", [ModelRequest(parts=[UserPromptPart("question")])],
            agent_id=self.agent_id, user_id="owner", run_id="run", turn_id="turn")
        async with self.factory() as db:
            db.add(ChannelAccount(id="account", name="Account", agent_id=self.agent_id, session_user_id="owner"))
            await db.commit()
        self.app = FastAPI()
        for module in (diagnostic_sessions, diagnostic_channels, diagnostic_usage, diagnostics):
            self.app.include_router(module.router, prefix="/api/v1")
        ctx = SimpleNamespace(db_session_factory=self.factory, active_runs=ActiveRunRegistry(),
            store=self.stores.coordinator, stores=self.stores, registry=ToolPackageRegistry())
        self.app.dependency_overrides[get_app_context] = lambda: ctx
        self.app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=True)
        now = datetime.now(UTC)
        self.bounds = {"start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(minutes=1)).isoformat()}
        self.headers = {"user": "administrator"}
        self.mocks = ExitStack()
        async def profiles(_request, _caller, ids):
            return {uid: {"user_id": uid, "display_name": uid, "resolution": "resolved"} for uid in ids}
        self.mocks.enter_context(patch("deep_agent.security.diagnostic_users.resolve_diagnostic_users", profiles))
        self.mocks.enter_context(patch("deep_agent.api.v1.diagnostics._runtime", return_value=(None, None)))
        self.mocks.enter_context(patch("deep_agent.api.v1.diagnostics.query_logs", side_effect=lambda _config, **kw: {"selected": kw}))

    async def asyncTearDown(self):
        self.mocks.close()
        await self.engine.dispose()

    def cases(self):
        return [
            ("sessions", {**self.bounds, "query": "session", "user_id": "owner", "limit": 1}),
            ("sessions/overview", self.bounds),
            ("sessions/users", {**self.bounds, "query": "owner", "after": "a"}),
            ("sessions/user-profile", {"user_id": "owner"}),
            ("sessions/session", {"include_context": False}),
            ("sessions/session/events", {"after_sequence": 0, "limit": 1, "run_id": "run"}),
            ("usage", self.bounds),
            ("usage/details", {**self.bounds, "group_by": "agent", "limit": 1, "offset": 0}),
            ("usage/sessions", {**self.bounds, "group_by": "agent", "key": self.agent_id}),
            ("usage/export", {**self.bounds, "group_by": "agent", "scope": "page"}),
            ("logs", {**self.bounds, "q": "private query", "session_id": "session", "limit": 1}),
            ("channels/account/overview", self.bounds),
            ("channels/account/peers", {**self.bounds, "kind": "group", "options_only": True, "peer_id": "peer"}),
            ("channels/account/sessions", {**self.bounds, "kind": "user", "peer_id": "peer", "sender_id": "sender"}),
        ]

    async def test_json_reads_match_get_and_never_issue_database_writes(self):
        statements = []
        def capture(_conn, _cursor, sql, *_args):
            statements.append(sql)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                for path, body in self.cases():
                    with self.subTest(path=path):
                        url = "/api/v1/diagnostics/" + path
                        get = await client.get(url, params=body, headers=self.headers)
                        post = await client.post(url, json=body, headers=self.headers)
                        self.assertEqual(get.status_code, 200, get.text)
                        self.assertEqual(post.status_code, 200, post.text)
                        self.assertEqual(post.request.url.query, b"")
                        self.assertEqual(post.headers["cache-control"], "no-store")
                        if path == "usage/export":
                            self.assertEqual(get.text, post.text)
                        else:
                            left, right = get.json(), post.json()
                            left.pop("generated_at", None)
                            right.pop("generated_at", None)
                            self.assertEqual(left, right)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements), statements)

    async def test_all_json_queries_keep_admin_gate_and_reject_anonymous(self):
        for route in self.app.routes:
            if "POST" in (getattr(route, "methods", None) or {}):
                self.assertIn(require_console_admin, [dep.call for dep in route.dependant.dependencies])
        self.app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            for path, body in self.cases():
                response = await client.post("/api/v1/diagnostics/" + path, json=body, headers={"user": "ordinary"})
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.headers["cache-control"], "no-store")
            self.app.dependency_overrides.pop(resolve_console_admin_access)
            for path, body in self.cases():
                response = await client.post("/api/v1/diagnostics/" + path, json=body)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_invalid_json_filters_are_private_and_keep_existing_validation(self):
        cases = [("sessions", {"limit": 201}), ("sessions", {**self.bounds, "timezone": "invalid"}),
            ("sessions", {"query": "PRIVATE_INVALID_INPUT", "unknown": "PRIVATE_INVALID_INPUT"}),
            ("sessions", {"start": "PRIVATE_INVALID_INPUT"}),
            ("sessions", {"start": self.bounds["end"], "end": self.bounds["start"]}),
            ("sessions/user-profile", {"user_id": " "}), ("sessions/users", {"after": "x" * 129}),
            ("sessions/session/events", {"limit": 501}), ("usage/details", {"group_by": "invalid"}),
            ("usage/sessions", {"group_by": "agent"}), ("usage/export", {"scope": "invalid"}),
            ("logs", {"limit": 501}), ("channels/account/peers", {"kind": "invalid"}),
            ("channels/account/sessions", {"peer_id": ""})]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            for path, body in cases:
                response = await client.post("/api/v1/diagnostics/" + path, json=body, headers=self.headers)
                self.assertEqual(response.status_code, 422, (path, response.text))
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("PRIVATE_INVALID_INPUT", response.text)

    async def test_read_only_preview_allows_only_exact_diagnostic_query_aliases(self):
        preview = LocalIdentityAdapter(self.app, LocalIdentity(user="admin"), read_only_preview=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview), base_url="http://test") as client:
            for path, body in self.cases():
                response = await client.post("/api/v1/diagnostics/" + path, json=body)
                self.assertEqual(response.status_code, 200, (path, response.text))
            for path in ("/api/v1/chat", "/api/v1/agents", "/api/v1/diagnostics/logs/status",
                         "/api/v1/diagnostics/channels/accounts", "/api/v1/diagnostics/sessions/session/history",
                         "/api/v1/diagnostics/sessions/session/cancel", "/api/v1/diagnostics/usage/delete"):
                response = await client.post(path, json={})
                self.assertEqual(response.status_code, 403, path)
            for method in ("put", "delete", "patch"):
                response = await client.request(method, "/api/v1/diagnostics/sessions/session", json={})
                self.assertEqual(response.status_code, 403)
