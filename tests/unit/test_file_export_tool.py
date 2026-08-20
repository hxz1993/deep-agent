from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.files import build_file_export_toolset
from deep_agent.schemas.attachment import AttachmentRef


def _context(
    *,
    attachments: tuple[AttachmentRef, ...] = (),
    artifacts: dict[str, dict] | None = None,
    headers: dict[str, str] | None = None,
) -> RunContext[AgentDeps]:
    request_headers = headers or {
        "authorization": "Bearer secret",
        "user": "user-1",
        "app": "app001",
        "space": "app",
    }
    return RunContext(
        deps=AgentDeps(
            session_id="file-export-test",
            user="user-1",
            request_headers=request_headers,
            attachment_by_id={item.id: item for item in attachments},
            tool_output_artifacts=artifacts or {},
        ),
        model=TestModel(),
        usage=RunUsage(),
    )


async def _tool(ctx: RunContext[AgentDeps]):
    tools = await build_file_export_toolset().get_tools(ctx)
    return tools.get("export_file_to_download_center")


class FileExportToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_directly_visible_with_platform_context_and_hidden_without_it(self) -> None:
        tool = await _tool(_context())
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(
            set(tool.tool_def.parameters_json_schema["properties"]),
            {"source", "file_name"},
        )
        hidden = _context(headers={"authorization": "Bearer secret"})
        hidden.deps.user = None
        self.assertIsNone(await _tool(hidden))

    async def test_generated_content_uploads_then_registers_download_record(self) -> None:
        ctx = _context()
        tool = await _tool(ctx)
        assert tool is not None
        upload = AsyncMock(
            return_value=SimpleNamespace(
                id="generated-file-1",
                fileName="generated.html",
                fileSize=18,
            )
        )
        delete = AsyncMock()
        save = AsyncMock(return_value="download-record-1")
        platform = SimpleNamespace(file=SimpleNamespace(upload=upload, delete_file=delete))
        space = SimpleNamespace(business_record=SimpleNamespace(save=save))

        with patch(
            "deep_agent.files.export_toolset.PlatformAPI",
            return_value=platform,
        ), patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=space,
        ):
            result = await tool.call_func(
                {
                    "source": {"type": "content", "content": "<h1>报告</h1>"},
                    "file_name": "generated-content-test.html",
                },
                ctx,
            )

        self.assertEqual(result.source_type, "content")
        self.assertEqual(result.file_id, "generated-file-1")
        self.assertEqual(result.download_record_id, "download-record-1")
        upload.assert_awaited_once()
        self.assertEqual(upload.await_args.kwargs["file_type"], "DL")
        self.assertEqual(upload.await_args.kwargs["file"], "<h1>报告</h1>".encode())
        record = save.await_args.args[0]
        self.assertEqual(record["fileId"], "generated-file-1")
        self.assertEqual(record["fileName"], "generated-content-test.html")
        self.assertEqual(record["fileDescription"], "generated-content-test.html")
        self.assertEqual(record["opeTypeDesc"], "Agent文件导出")
        delete.assert_not_awaited()

    async def test_existing_attachment_registers_without_reupload(self) -> None:
        ref = AttachmentRef(id="attachment-1", name="source.md", mime="text/markdown")
        ctx = _context(attachments=(ref,))
        tool = await _tool(ctx)
        assert tool is not None
        save = AsyncMock(return_value="download-record-2")
        space = SimpleNamespace(business_record=SimpleNamespace(save=save))

        with patch(
            "deep_agent.files.export_toolset._download_file",
            new_callable=AsyncMock,
            return_value=b"# Existing attachment",
        ) as download, patch(
            "deep_agent.files.export_toolset.PlatformAPI",
        ) as platform, patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=space,
        ):
            result = await tool.call_func(
                {
                    "source": {"type": "platform_file", "file_id": ref.id},
                    "file_name": "existing-attachment-test.md",
                },
                ctx,
            )

        self.assertEqual(result.source_type, "platform_file")
        self.assertEqual(result.file_id, ref.id)
        self.assertEqual(result.byte_size, len(b"# Existing attachment"))
        download.assert_awaited_once()
        platform.assert_not_called()
        self.assertEqual(save.await_args.args[0]["fileId"], ref.id)

    async def test_existing_artifact_uses_history_metadata_without_download(self) -> None:
        ctx = _context(
            artifacts={
                "artifact-1": {
                    "artifact_id": "artifact-1",
                    "kind": "tool_result_overflow",
                    "byte_size": 4096,
                }
            }
        )
        tool = await _tool(ctx)
        assert tool is not None
        save = AsyncMock(return_value="download-record-3")
        space = SimpleNamespace(business_record=SimpleNamespace(save=save))
        with patch(
            "deep_agent.files.export_toolset._download_file",
            new_callable=AsyncMock,
        ) as download, patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=space,
        ):
            result = await tool.call_func(
                {
                    "source": {"type": "platform_file", "file_id": "artifact-1"},
                    "file_name": "existing-artifact-test.json",
                },
                ctx,
            )
        self.assertEqual(result.byte_size, 4096)
        download.assert_not_awaited()

    async def test_unknown_file_id_is_rejected_before_platform_calls(self) -> None:
        ctx = _context()
        tool = await _tool(ctx)
        assert tool is not None
        with patch("deep_agent.files.export_toolset.SpaceAPI") as space:
            with self.assertRaises(ModelRetry):
                await tool.call_func(
                    {
                        "source": {"type": "platform_file", "file_id": "unknown"},
                        "file_name": "unknown.txt",
                    },
                    ctx,
                )
        space.assert_not_called()

    async def test_registration_failure_removes_only_newly_uploaded_file(self) -> None:
        ctx = _context()
        tool = await _tool(ctx)
        assert tool is not None
        upload = AsyncMock(return_value=SimpleNamespace(id="orphan-1", fileSize=4))
        delete = AsyncMock()
        save = AsyncMock(side_effect=RuntimeError("platform unavailable"))
        platform = SimpleNamespace(file=SimpleNamespace(upload=upload, delete_file=delete))
        space = SimpleNamespace(business_record=SimpleNamespace(save=save))
        with patch(
            "deep_agent.files.export_toolset.PlatformAPI",
            return_value=platform,
        ), patch(
            "deep_agent.files.export_toolset.SpaceAPI",
            return_value=space,
        ):
            with self.assertRaises(ToolFailed):
                await tool.call_func(
                    {
                        "source": {"type": "content", "content": "test"},
                        "file_name": "cleanup-test.txt",
                    },
                    ctx,
                )
        delete.assert_awaited_once_with("orphan-1")

    async def test_file_name_must_be_a_plain_name_with_extension(self) -> None:
        ctx = _context()
        tool = await _tool(ctx)
        assert tool is not None
        for file_name in ("../report.html", "report", "folder/report.html"):
            with self.subTest(file_name=file_name), self.assertRaises(ModelRetry):
                await tool.call_func(
                    {
                        "source": {"type": "content", "content": "test"},
                        "file_name": file_name,
                    },
                    ctx,
                )


if __name__ == "__main__":
    unittest.main()
