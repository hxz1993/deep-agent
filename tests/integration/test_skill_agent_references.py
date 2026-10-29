from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.admin import require_console_admin, resolve_console_admin_access
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import Agent
from deep_agent.security import ConsoleAdminAccess
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores


class SkillAgentReferenceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, self.session_factory = await make_sqlite_stores()
        self.app = FastAPI()
        install_test_user_header(self.app)
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        init_context(AppContext.for_testing(
            registry=build_registry(), stores=self.stores,
            db_engine=self.engine, db_session_factory=self.session_factory,
        ))
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_empty_directory_and_existing_skill_list_contract(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            references = await client.get("/api/v1/skills/references/agents")
            self.assertEqual(references.status_code, 200, references.text)
            self.assertEqual(references.json(), [])
            skills = await client.get("/api/v1/skills")
            self.assertEqual(skills.status_code, 200, skills.text)
            self.assertTrue(skills.json())
            self.assertTrue(all("agents" not in skill and "skills" not in skill for skill in skills.json()))
            # The literal route must not fall through to /skills/{code}.
            self.assertIn("/api/v1/skills/references/agents", self.app.openapi()["paths"])

    async def test_reference_directory_does_not_shadow_valid_skill_codes(self) -> None:
        from tests.integration.test_skill_api import _FakePlatformAPI, _FakePlatformFiles
        from tests.unit.test_skill_packages import skill_zip

        _FakePlatformFiles.blobs = {}
        _FakePlatformFiles.deleted = []
        with patch("deep_agent.files.storage.PlatformAPI", _FakePlatformAPI):
            async with AsyncClient(transport=self.transport, base_url="http://test",
                                   headers={"app": "iirjgi005", "space": "iirjgi"}) as client:
                for code in ("agent-references", "references"):
                    installed = await client.post("/api/v1/skills", files={
                        "file": (f"{code}.zip", skill_zip(name=code), "application/zip"),
                    })
                    self.assertEqual(installed.status_code, 201, installed.text)
                    detail = await client.get(f"/api/v1/skills/{code}")
                    self.assertEqual(detail.status_code, 200, detail.text)
                    self.assertEqual(detail.json()["code"], code)
                    self.assertIn("resources", detail.json())
                directory = await client.get("/api/v1/skills/references/agents")
                self.assertEqual(directory.status_code, 200, directory.text)
                self.assertEqual(directory.json(), [])

    async def test_single_lightweight_query_preserves_references_without_writing(self) -> None:
        original_skills = ["deepfos-readonly-analysis", "obsolete-skill", "deepfos-readonly-analysis", "", None, 4]
        async with self.session_factory() as db:
            db.add_all([
                Agent(id="1" * 32, name="甲助手", prompt="private prompt", skills=original_skills,
                      updated_by="must-not-resolve"),
                Agent(id="2" * 32, name="乙助手", prompt="another private prompt",
                      skills=["deepfos-readonly-analysis"]),
                Agent(id="3" * 32, name="空助手", prompt="", skills=[]),
            ])
            await db.commit()
        statements: list[str] = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            # These broader paths must not be used to build the reference directory.
            with patch.object(self.stores.agent, "list_all", AsyncMock(side_effect=AssertionError("full Agent read"))), \
                 patch("deep_agent.api.v1.agents.resolve_diagnostic_users", AsyncMock(side_effect=AssertionError("profile lookup"))):
                async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                    response = await client.get("/api/v1/skills/references/agents")
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 200, response.text)
        result = {row["id"]: row for row in response.json()}
        self.assertEqual(set(result), {"1" * 32, "2" * 32, "3" * 32})
        self.assertTrue(all(set(row) == {"id", "name", "skills"} for row in result.values()))
        self.assertEqual(result["1" * 32]["skills"], ["deepfos-readonly-analysis", "obsolete-skill"])
        self.assertEqual(result["2" * 32]["skills"], ["deepfos-readonly-analysis"])
        self.assertEqual(result["3" * 32]["skills"], [])
        self.assertNotIn("private prompt", response.text)
        self.assertEqual(len(statements), 1, statements)
        sql = " ".join(statements[0].lower().split())
        self.assertTrue(sql.startswith("select agents.id, agents.name, agents.skills from agents"), sql)
        for forbidden in ("prompt", "llm_models", "agent_output_configs", "updated_by", "insert", "update ", "delete"):
            self.assertNotIn(forbidden, sql)
        # Deduplication is a response-only projection, not a data repair.
        async with self.session_factory() as db:
            saved = await db.scalar(select(Agent.skills).where(Agent.id == "1" * 32))
        self.assertEqual(saved, original_skills)

    async def test_admin_gate_rejects_non_admin_and_invalid_identity_before_query(self) -> None:
        self.app.dependency_overrides.pop(require_console_admin)

        async def denied():
            return ConsoleAdminAccess(allowed=False, roles=())

        async def invalid():
            raise HTTPException(status_code=401, detail="invalid identity")

        with patch.object(self.stores.agent, "list_skill_references", AsyncMock(side_effect=AssertionError("unauthorized query"))):
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                for resolver, expected in ((denied, 403), (invalid, 401)):
                    self.app.dependency_overrides[resolve_console_admin_access] = resolver
                    response = await client.get("/api/v1/skills/references/agents")
                    self.assertEqual(response.status_code, expected, response.text)


if __name__ == "__main__":
    unittest.main()
