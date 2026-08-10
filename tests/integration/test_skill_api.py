from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


class _FakePlatformFiles:
    blobs: dict[str, bytes] = {}
    deleted: list[str] = []

    async def upload(self, *, file_type: str, file_name: str, file: bytes):
        file_id = f"file-{len(self.blobs) + 1}"
        self.blobs[file_id] = bytes(file)
        return SimpleNamespace(id=file_id, fileName=file_name, fileSize=len(file))

    async def download(self, file_id: str) -> bytes:
        return self.blobs[file_id]

    async def delete_file(self, file_id: str):
        self.deleted.append(file_id)
        self.blobs.pop(file_id, None)


class _FakePlatformAPI:
    def __init__(self, header=None):
        self.header = header
        self.file = _FakePlatformFiles()


class SkillApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _FakePlatformFiles.blobs = {}
        _FakePlatformFiles.deleted = []
        self.stores, self.engine, session_factory = await make_sqlite_stores()
        self.app = FastAPI()
        self.app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            )
        )
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)
        self.platform_patch = patch("deep_agent.api.v1.skills.PlatformAPI", _FakePlatformAPI)
        self.platform_patch.start()
        self.runtime_platform_patch = patch(
            "deep_agent.skills.runtime.PlatformAPI", _FakePlatformAPI
        )
        self.runtime_platform_patch.start()

    async def asyncTearDown(self) -> None:
        self.platform_patch.stop()
        self.runtime_platform_patch.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_install_configure_read_and_delete_protection(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example-skill.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201, installed.text)
            self.assertEqual(installed.json()["status"], "installed")
            self.assertEqual(installed.json()["skill"]["source"], "upload")

            listed = await client.get("/api/v1/skills")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(
                {item["code"] for item in listed.json()},
                {"deepagent-diagnostics", "deepfos-readonly-analysis", "example-skill"},
            )

            resources = await client.get("/api/v1/skills/example-skill/resources")
            self.assertEqual(resources.status_code, 200)
            self.assertNotIn("script", {item["category"] for item in resources.json()})
            text = await client.get(
                "/api/v1/skills/example-skill/resources/references/guide.md",
                params={"limit": 8},
            )
            self.assertEqual(text.status_code, 200, text.text)
            self.assertTrue(text.json()["has_more"])

            model_id = (await self.stores.llm_model.list_all())[0].id
            agent = await client.post(
                "/api/v1/agents",
                json={
                    "name": "skill-agent",
                    "model_id": model_id,
                    "prompt": "Use selected skills only when relevant.",
                    "skills": ["example-skill", "deepagent-diagnostics"],
                },
            )
            self.assertEqual(agent.status_code, 201, agent.text)
            self.assertEqual(agent.json()["skills"], ["example-skill", "deepagent-diagnostics"])

            conflict = await client.delete("/api/v1/skills/example-skill")
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["detail"]["agents"], ["skill-agent"])

            cleared = await client.patch(
                f"/api/v1/agents/{agent.json()['id']}",
                json={"skills": []},
            )
            self.assertEqual(cleared.status_code, 200)
            deleted = await client.delete("/api/v1/skills/example-skill")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(_FakePlatformFiles.deleted)

    async def test_builtin_is_read_only_and_disabled_upload_cannot_be_selected(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            forbidden = await client.patch(
                "/api/v1/skills/deepagent-diagnostics",
                json={"enabled": False},
            )
            self.assertEqual(forbidden.status_code, 403)

            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201)
            disabled = await client.patch(
                "/api/v1/skills/example-skill",
                json={"enabled": False},
            )
            self.assertEqual(disabled.status_code, 200)
            model_id = (await self.stores.llm_model.list_all())[0].id
            rejected = await client.post(
                "/api/v1/agents",
                json={
                    "name": "disabled-skill-agent",
                    "model_id": model_id,
                    "prompt": "test",
                    "skills": ["example-skill"],
                },
            )
            self.assertEqual(rejected.status_code, 422)
            self.assertEqual(rejected.json()["detail"]["invalid_skills"], ["example-skill"])

    async def test_existing_disabled_skill_reference_can_be_preserved_but_not_added(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201)
            model_id = (await self.stores.llm_model.list_all())[0].id
            agent = await client.post(
                "/api/v1/agents",
                json={
                    "name": "preserve-disabled-skill",
                    "model_id": model_id,
                    "prompt": "test",
                    "skills": ["example-skill"],
                },
            )
            self.assertEqual(agent.status_code, 201, agent.text)
            await client.patch("/api/v1/skills/example-skill", json={"enabled": False})

            preserved = await client.patch(
                f"/api/v1/agents/{agent.json()['id']}",
                json={"name": "preserved", "skills": ["example-skill"]},
            )
            self.assertEqual(preserved.status_code, 200, preserved.text)
            self.assertEqual(preserved.json()["skills"], ["example-skill"])

            another = await client.post(
                "/api/v1/agents",
                json={"name": "another", "model_id": model_id, "prompt": "test"},
            )
            rejected = await client.patch(
                f"/api/v1/agents/{another.json()['id']}",
                json={"skills": ["example-skill"]},
            )
            self.assertEqual(rejected.status_code, 422)

    async def test_same_hash_update_is_noop_without_new_upload(self) -> None:
        payload = skill_zip()
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", payload, "application/zip")},
            )
            before = dict(_FakePlatformFiles.blobs)
            response = await client.put(
                "/api/v1/skills/example-skill/package",
                files={"file": ("example.zip", payload, "application/zip")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "noop")
            self.assertEqual(_FakePlatformFiles.blobs, before)

    async def test_patch_rejects_explicit_null(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201)
            for field in ("label", "description", "enabled"):
                response = await client.patch(
                    "/api/v1/skills/example-skill",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, (field, response.text))


if __name__ == "__main__":
    unittest.main()
