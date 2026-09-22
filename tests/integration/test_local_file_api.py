from __future__ import annotations

import shutil
import tempfile
import unittest
import io
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from PIL import Image
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from deep_agent.agent.attachments import resolve_user_prompt
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import PlatformToolResultStore
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.files import build_file_export_toolset, build_file_toolset
from deep_agent.files.storage import LocalFileStore, PlatformFileStore
from deep_agent.schemas.attachment import AttachmentRef
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


class LocalFileApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="local-file-api-TMP_to_delete-"))
        self.file_store = LocalFileStore(self.root)
        await self.file_store.initialize()
        self.stores, self.engine, factory = await make_sqlite_stores()
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=factory,
                file_store=self.file_store,
            )
        )
        self.app = FastAPI()
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()
        shutil.rmtree(self.root, ignore_errors=True)

    async def test_upload_protocol_context_and_authenticated_local_download(self) -> None:
        headers = {
            "user": "local-user",
            "X-Deep-Agent-App": "app001",
            "X-Deep-Agent-Space": "app",
            "X-Deep-Agent-Language": "zh-CN",
        }
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            uploaded = await client.post(
                "/api/v1/uploads",
                files={"file": ("说明.txt", "本地附件".encode(), "text/plain")},
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            payload = uploaded.json()
            self.assertEqual(set(payload), {"id", "name", "mime", "size", "extracted_text"})
            self.assertTrue(payload["id"].startswith("local_"))
            downloaded = await client.get(
                f"/api/v1/local-files/{payload['id']}/download"
            )
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(downloaded.content, "本地附件".encode())
            metadata = await self.file_store.describe(payload["id"], {"user": "local-user"})
            self.assertEqual(metadata.app, "app001")
            self.assertEqual(metadata.space, "app")

        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers={"user": "other-user"},
        ) as client:
            denied = await client.get(
                f"/api/v1/local-files/{payload['id']}/download"
            )
        self.assertEqual(denied.status_code, 404)

    async def test_channel_upload_uses_verified_current_application_and_owner(self):
        import os
        from unittest.mock import patch
        from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
        from tests.helpers.sqlite_stores import seed_default_agent

        agent_id = await seed_default_agent(self.stores)
        account = await self.stores.channel.create_account(ChannelAccountCreate(
            name="file scope", bot_id="file-scope-fixture", secret="fixture", agent_id=agent_id,
            session_user_id="channel-owner", enabled=True,
        ))
        headers = {"user": "forged-owner", "app": "bad", "space": "bad",
                   "x-deep-agent-app": "forged001", "x-deep-agent-space": "forged",
                   "x-deep-agent-channel-account": account.id,
                   "x-deep-agent-channel-token": "fixture-internal"}
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}):
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                for app_id in ("first001", "second002", None):
                    await self.stores.channel.update_account(account.id, ChannelAccountUpdate(settings={
                        "deepfos": {"enabled": app_id is not None, "app_id": app_id},
                    }))
                    response = await client.post("/api/v1/uploads", headers=headers,
                                                 files={"file": ("log.txt", b"sample", "text/plain")})
                    self.assertEqual(response.status_code, 200, response.text)
                    metadata = await self.file_store.describe(response.json()["id"], {"user": "channel-owner"})
                    self.assertEqual(metadata.app, app_id)
                    self.assertEqual(metadata.space, app_id[:-3] if app_id else None)
                headers["x-deep-agent-channel-token"] = "wrong"
                denied = await client.post("/api/v1/uploads", headers=headers,
                                           files={"file": ("log.txt", b"sample", "text/plain")})
                self.assertEqual(denied.status_code, 401)

    async def test_platform_backend_never_exposes_local_route(self) -> None:
        get_context().file_store = PlatformFileStore()
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers={"user": "local-user"},
        ) as client:
            response = await client.get(
                "/api/v1/local-files/local_00000000000000000000000000000000/download"
            )
        self.assertEqual(response.status_code, 404)

    async def test_admin_download_is_scoped_to_session_files_and_original_owner(self):
        from deep_agent.agent.attachments import attachment_ref_dict
        from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT, FILE_TYPE_SKILL_PACKAGE
        from tests.helpers.sqlite_stores import seed_default_agent

        agent_id = await seed_default_agent(self.stores)
        async def upload(name, owner="session-owner", file_type=FILE_TYPE_ATTACHMENT):
            return await self.file_store.upload(file_type=file_type, file_name=name,
                data=name.encode(), mime="text/plain", request_headers={"user": owner})

        attachment = await upload("input.txt")
        generated = await upload("output.txt")
        overflow = await upload("overflow.txt")
        unrelated = await upload("unrelated.txt")
        foreign = await upload("foreign.txt", owner="another-owner")
        system = await upload("system.zip", file_type=FILE_TYPE_SKILL_PACKAGE)
        sid = "diagnostic-files"
        refs = [AttachmentRef(id=item.id, name=item.file_name, mime="text/plain")
                for item in (attachment, foreign, system)]
        await get_context().store.append(sid, [ModelRequest(parts=[
            UserPromptPart(["fixture", *[attachment_ref_dict(ref) for ref in refs]]),
            ToolReturnPart("execute_python", "done", tool_call_id="output-call", metadata={
                "generated_files": [{"file_id": generated.id, "file_name": generated.file_name}],
                "overflow_handle": overflow.id,
            }),
            # Arbitrary text is not a trusted file reference.
            ToolReturnPart("fake", {"file_id": unrelated.id}, tool_call_id="fake-call"),
        ])], agent_id=agent_id, user_id="session-owner", run_id="fixture-run")
        await get_context().store.append("other-session", [ModelRequest(parts=[UserPromptPart("hello")])],
            agent_id=agent_id, user_id="session-owner", run_id="other-run")
        path = f"/api/v1/diagnostics/sessions/{sid}/files"
        async with AsyncClient(transport=self.transport, base_url="http://test",
                               headers={"user": "admin-caller"}) as client:
            for item in (attachment, generated, overflow):
                response = await client.get(f"{path}/{item.id}/download")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, item.file_name.encode())
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn("attachment;", response.headers["content-disposition"])
                ordinary = await client.get(f"/api/v1/local-files/{item.id}/download")
                self.assertEqual(ordinary.status_code, 404)
            for item in (unrelated, foreign, system):
                response = await client.get(f"{path}/{item.id}/download")
                self.assertEqual(response.status_code, 404, response.text)
            for target in ("other-session", "missing-session"):
                response = await client.get(f"/api/v1/diagnostics/sessions/{target}/files/{attachment.id}/download")
                self.assertEqual(response.status_code, 404)
            get_context().file_store = PlatformFileStore()
            response = await client.get(f"{path}/{attachment.id}/download")
            self.assertEqual(response.status_code, 404)

    async def test_diagnostic_file_download_requires_admin(self):
        from deep_agent.api.v1.admin import require_console_admin, resolve_console_admin_access
        from deep_agent.security import ConsoleAdminAccess

        self.app.dependency_overrides.pop(require_console_admin)
        self.app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False, roles=())
        async with AsyncClient(transport=self.transport, base_url="http://test", headers={"user": "ordinary"}) as client:
            response = await client.get("/api/v1/diagnostics/sessions/any/files/local_any/download")
            self.assertEqual(response.status_code, 403)

    async def test_local_read_file_native_image_spill_skill_and_export(self) -> None:
        headers = {"user": "local-user", "app": "app001", "space": "app"}
        document = Document()
        document.add_paragraph("本地 Word 内容")
        docx = io.BytesIO()
        document.save(docx)
        workbook = Workbook()
        workbook.active.append(["名称", "金额"])
        workbook.active.append(["华东", 100])
        xlsx = io.BytesIO()
        workbook.save(xlsx)
        png = io.BytesIO()
        Image.new("RGB", (2, 2), color=(68, 104, 179)).save(png, format="PNG")
        sources = (
            ("note.txt", "text/plain", "本地文本".encode()),
            ("server.log", "application/octet-stream", "本地日志".encode()),
            (
                "report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                docx.getvalue(),
            ),
            (
                "budget.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                xlsx.getvalue(),
            ),
            ("chart.png", "image/png", png.getvalue()),
        )
        refs: list[AttachmentRef] = []
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            for name, mime, data in sources:
                response = await client.post(
                    "/api/v1/uploads",
                    files={"file": (name, data, mime)},
                )
                self.assertEqual(response.status_code, 200, response.text)
                uploaded = response.json()
                refs.append(
                    AttachmentRef(
                        id=uploaded["id"],
                        name=uploaded["name"],
                        mime=uploaded["mime"],
                    )
                )
        self.assertEqual(refs[1].mime, "text/plain")

        deps = AgentDeps(
            session_id="local-file-tools",
            user="local-user",
            request_headers=headers,
            file_store=self.file_store,
            attachment_by_id={item.id: item for item in refs},
        )
        run_ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
        read_file = (await build_file_toolset().get_tools(run_ctx))["read_file"]
        expected = ("本地文本", "本地日志", "本地 Word 内容", "华东,100")
        for ref, marker in zip(refs[:4], expected):
            result = await read_file.call_func(
                {"resource": {"type": "attachment", "file_id": ref.id}},
                run_ctx,
            )
            self.assertIn(marker, result.content)
        native = await resolve_user_prompt(
            "识别图片",
            [refs[4]],
            headers,
            file_store=self.file_store,
        )
        self.assertEqual(native[1].media_type, "image/jpeg")
        self.assertTrue(native[1].data.startswith(b"\xff\xd8"))

        spill = PlatformToolResultStore(
            request_headers=headers,
            threshold_tokens=4_000,
            file_store=self.file_store,
        )
        spill.bind(deps, [])
        handle = await spill.write("run/call", "大工具结果".encode() * 5_000)
        self.assertTrue(handle.startswith("local_"))
        self.assertTrue((await spill.read(handle)).startswith("大工具结果".encode()))

        export_tool = (await build_file_export_toolset().get_tools(run_ctx))[
            "export_file_to_download_center"
        ]
        exported = await export_tool.call_func(
            {
                "source": {"type": "content", "content": "本地导出内容"},
                "file_name": "本地导出.txt",
            },
            run_ctx,
        )
        self.assertTrue(exported.file_id.startswith("local_"))
        self.assertTrue(exported.download_record_id.startswith("local_record_"))

        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            installed = await client.post(
                "/api/v1/skills",
                files={"file": ("local-skill.zip", skill_zip(), "application/zip")},
            )
            self.assertEqual(installed.status_code, 201, installed.text)
            stored_skill = await self.stores.skill_package.get_by_code("example-skill")
            assert stored_skill is not None
            skill_file_id = stored_skill.platform_file_id
            self.assertTrue(skill_file_id.startswith("local_"))
            package = await client.get("/api/v1/skills/example-skill/package")
            self.assertEqual(package.status_code, 200, package.text)
            deleted = await client.delete("/api/v1/skills/example-skill")
            self.assertEqual(deleted.status_code, 200, deleted.text)
        with self.assertRaises(OSError):
            await self.file_store.download(skill_file_id, {})


if __name__ == "__main__":
    unittest.main()
