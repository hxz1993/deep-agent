from __future__ import annotations

import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


class _FakePlatformFiles:
    blobs: dict[str, bytes] = {}
    deleted: list[str] = []
    uploaded_types: list[str] = []

    async def upload(self, *, file_type: str, file_name: str, file: bytes):
        self.uploaded_types.append(file_type)
        file_id = f"file-{len(self.blobs) + 1}"
        self.blobs[file_id] = bytes(file)
        return SimpleNamespace(id=file_id, fileName=file_name, fileSize=len(file))

    async def download(self, file_id: str) -> bytes:
        return self.blobs[file_id]

    async def delete_file(self, file_id: str):
        self.deleted.append(file_id)
        self.blobs.pop(file_id, None)


class _FakePlatformAPI:
    headers: list[dict | None] = []

    def __init__(self, header=None):
        self.header = header
        type(self).headers.append(header)
        self.file = _FakePlatformFiles()


class SkillApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _FakePlatformFiles.blobs = {}
        _FakePlatformFiles.deleted = []
        _FakePlatformFiles.uploaded_types = []
        _FakePlatformAPI.headers = []
        self.stores, self.engine, session_factory = await make_sqlite_stores()
        self.app = FastAPI()
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            )
        )
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)
        self.platform_patch = patch("deep_agent.files.storage.PlatformAPI", _FakePlatformAPI)
        self.platform_patch.start()
        self.client_headers = {"app": "iirjgi005", "space": "iirjgi"}

    async def asyncTearDown(self) -> None:
        self.platform_patch.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_install_configure_read_and_delete_protection(self) -> None:
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            installed = await client.post(
                "/api/v1/skills",
                files={
                    "file": (
                        "example-skill.zip",
                        skill_zip(scripts=True, office=True),
                        "application/zip",
                    )
                },
            )
            self.assertEqual(installed.status_code, 201, installed.text)
            self.assertEqual(installed.json()["status"], "installed")
            self.assertEqual(installed.json()["skill"]["source"], "upload")
            self.assertEqual(installed.json()["skill"]["runtime_status"], "ready")
            self.assertEqual(_FakePlatformFiles.uploaded_types, ["INF"])
            stored = await self.stores.skill_package.get_by_code("example-skill")
            self.assertNotIn("_deepagent_platform_space", stored.manifest)
            self.assertFalse(
                {"app", "space", "language"} & set(_FakePlatformAPI.headers[0])
            )

            reloaded = await client.post("/api/v1/skills/example-skill/reload")
            self.assertEqual(reloaded.status_code, 200, reloaded.text)
            self.assertEqual(reloaded.json()["runtime_status"], "ready")
            self.assertFalse(
                {"app", "space", "language"} & set(_FakePlatformAPI.headers[-1])
            )

            package = await client.get("/api/v1/skills/example-skill/package")
            self.assertEqual(package.status_code, 200, package.text)
            self.assertTrue(package.content.startswith(b"PK"))
            self.assertFalse(
                {"app", "space", "language"} & set(_FakePlatformAPI.headers[-1])
            )

            listed = await client.get("/api/v1/skills")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(
                {item["code"] for item in listed.json()},
                {"deepagent-diagnostics", "deepfos-readonly-analysis", "example-skill"},
            )
            self.assertTrue(
                all("script_execution_status" not in item for item in listed.json())
            )

            resources = await client.get("/api/v1/skills/example-skill/resources")
            self.assertEqual(resources.status_code, 200)
            self.assertIn("script", {item["category"] for item in resources.json()})
            by_path = {item["path"]: item for item in resources.json()}
            self.assertEqual(by_path["references/guide.docx"]["read_format"], "docx")
            self.assertEqual(by_path["assets/template.xlsx"]["read_format"], "xlsx")
            self.assertFalse(by_path["scripts/run.py"]["readable"])
            definition = await client.get(
                "/api/v1/skills/example-skill/resources/SKILL.md",
            )
            self.assertEqual(definition.status_code, 200, definition.text)
            self.assertEqual(definition.json()["path"], "SKILL.md")
            self.assertEqual(definition.json()["format"], "text")
            self.assertIn("example-skill", definition.json()["content"])
            text = await client.get(
                "/api/v1/skills/example-skill/resources/references/guide.md",
                params={"limit": 1},
            )
            self.assertEqual(text.status_code, 200, text.text)
            self.assertTrue(text.json()["has_more"])
            self.assertEqual(text.json()["unit"], "line")
            self.assertEqual(text.json()["next_offset"], 2)
            docx = await client.get(
                "/api/v1/skills/example-skill/resources/references/guide.docx",
                params={"offset": 1, "limit": 1},
            )
            self.assertEqual(docx.status_code, 200, docx.text)
            self.assertEqual(docx.json()["format"], "docx")
            self.assertEqual(docx.json()["content"], "Skill Word 第一段")
            xlsx = await client.get(
                "/api/v1/skills/example-skill/resources/assets/template.xlsx",
                params={"offset": 1, "limit": 2},
            )
            self.assertEqual(xlsx.status_code, 200, xlsx.text)
            self.assertEqual(xlsx.json()["format"], "xlsx")
            self.assertEqual(xlsx.json()["content"], "名称,值\n甲,1")

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

    async def test_export_one_and_all_uploaded_skills_as_installable_packages(self) -> None:
        first = skill_zip(name="first-skill")
        second = skill_zip(name="second-skill", scripts=True)
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            for name, payload in (("first-skill", first), ("second-skill", second)):
                installed = await client.post(
                    "/api/v1/skills",
                    files={"file": (f"{name}.zip", payload, "application/zip")},
                )
                self.assertEqual(installed.status_code, 201, installed.text)

            single = await client.get("/api/v1/skills/first-skill/package")
            self.assertEqual(single.status_code, 200, single.text)
            self.assertEqual(single.content, first)

            exported = await client.get("/api/v1/skills/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.headers["content-type"], "application/zip")

        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["manifest.json", "skills/first-skill.zip", "skills/second-skill.zip"],
            )
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], "deepagent-skill-export")
            self.assertEqual(manifest["schema_version"], "1.0")
            self.assertEqual(
                [item["code"] for item in manifest["skills"]],
                ["first-skill", "second-skill"],
            )
            self.assertEqual(archive.read("skills/first-skill.zip"), first)
            self.assertEqual(archive.read("skills/second-skill.zip"), second)

    async def test_export_all_requires_uploaded_skills(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.get("/api/v1/skills/export")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "No uploaded Skills to export")

    async def test_install_same_code_conflicts_and_replace_requires_same_code(self) -> None:
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201, installed.text)

            duplicate = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            self.assertIn("Skill already exists", duplicate.json()["detail"])

            wrong_code = await client.put(
                "/api/v1/skills/example-skill/package",
                files={
                    "file": (
                        "different.zip",
                        skill_zip(name="different-skill"),
                        "application/zip",
                    )
                },
            )
            self.assertEqual(wrong_code.status_code, 422, wrong_code.text)
            self.assertIn("must remain 'example-skill'", wrong_code.json()["detail"])

    async def test_builtin_is_read_only_and_uploaded_skill_can_be_selected(self) -> None:
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            forbidden = await client.patch(
                "/api/v1/skills/deepagent-diagnostics",
                json={"label": "不能修改"},
            )
            self.assertEqual(forbidden.status_code, 403)

            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201)
            obsolete_field = await client.patch(
                "/api/v1/skills/example-skill",
                json={"enabled": False},
            )
            self.assertEqual(obsolete_field.status_code, 422)
            reloaded = await client.post("/api/v1/skills/example-skill/reload")
            self.assertEqual(reloaded.status_code, 200)
            model_id = (await self.stores.llm_model.list_all())[0].id
            accepted = await client.post(
                "/api/v1/agents",
                json={
                    "name": "configured-skill-agent",
                    "model_id": model_id,
                    "prompt": "test",
                    "skills": ["example-skill"],
                },
            )
            self.assertEqual(accepted.status_code, 201, accepted.text)

    async def test_uploaded_skill_can_be_associated_with_multiple_agents(self) -> None:
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201)
            model_id = (await self.stores.llm_model.list_all())[0].id
            agent = await client.post(
                "/api/v1/agents",
                json={
                    "name": "first-skill-agent",
                    "model_id": model_id,
                    "prompt": "test",
                    "skills": ["example-skill"],
                },
            )
            self.assertEqual(agent.status_code, 201, agent.text)

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
            associated = await client.patch(
                f"/api/v1/agents/{another.json()['id']}",
                json={"skills": ["example-skill"]},
            )
            self.assertEqual(associated.status_code, 200, associated.text)
            self.assertEqual(associated.json()["skills"], ["example-skill"])

    async def test_same_hash_update_is_noop_without_new_upload(self) -> None:
        payload = skill_zip()
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
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

    async def test_same_hash_update_repairs_missing_platform_file(self) -> None:
        payload = skill_zip()
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", payload, "application/zip")},
            )
            self.assertEqual(installed.status_code, 201, installed.text)
            original = await self.stores.skill_package.get_by_code("example-skill")
            _FakePlatformFiles.blobs.pop(original.platform_file_id)
            _FakePlatformFiles.blobs["unrelated-file"] = b"unrelated"

            response = await client.put(
                "/api/v1/skills/example-skill/package",
                files={"file": ("example.zip", payload, "application/zip")},
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "repaired")
            self.assertEqual(response.json()["skill"]["runtime_status"], "ready")
            repaired = await self.stores.skill_package.get_by_code("example-skill")
            self.assertNotEqual(repaired.platform_file_id, original.platform_file_id)
            self.assertEqual(
                _FakePlatformFiles.blobs[repaired.platform_file_id],
                payload,
            )

    async def test_patch_rejects_explicit_null(self) -> None:
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=self.client_headers,
        ) as client:
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

    async def test_install_without_space_uses_system_scoped_platform_file(self) -> None:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/skills",
                files={"file": ("example.zip", skill_zip(), "application/zip")},
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("space", _FakePlatformAPI.headers[0])
        self.assertEqual(len(_FakePlatformFiles.blobs), 1)


if __name__ == "__main__":
    unittest.main()
