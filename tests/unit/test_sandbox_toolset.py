from __future__ import annotations

import asyncio
import hashlib
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai import ModelRetry, RunContext, ToolFailed, ToolReturn
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.options import OPTION
from deep_agent.sandbox.factory import SandboxInterpreterError, build_sandbox_backend
from deep_agent.sandbox.toolset import build_python_sandbox_tool_package
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.types import (
    ExecuteRequest,
    ExecuteResult,
    SandboxPublishedFile,
    TruncationMetadata,
)
from deep_agent.files.storage import StoredFile
from deep_agent.schemas.attachment import AttachmentRef
from tests.helpers.sandbox import sandbox_test_python
from deepfos_platform.errors.classes import APIStatusNotTrue


def _context(
    *,
    user: str | None = "user-1",
    headers: dict[str, str] | None = None,
    attachment_bytes: dict[str, bytes] | None = None,
    file_store: Any | None = None,
    session_artifacts: dict[str, dict[str, Any]] | None = None,
) -> RunContext[AgentDeps]:
    refs = {}
    if attachment_bytes:
        for file_id, data in attachment_bytes.items():
            refs[file_id] = AttachmentRef(id=file_id, name=file_id, mime="text/plain")
    return RunContext(
        deps=AgentDeps(
            session_id="sandbox-test",
            user=user,
            request_headers=headers or {"authorization": "Bearer secret", "cookie": "sid=1"},
            attachment_by_id=refs,
            session_file_artifacts=session_artifacts or {},
            file_bytes={f"attachment:{key}": value for key, value in (attachment_bytes or {}).items()},
            file_store=file_store,
            context_state=MagicMock(),
        ),
        model=TestModel(),
        usage=RunUsage(),
    )


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.requests: list[ExecuteRequest] = []
        self.health = AsyncMock(
            return_value=MagicMock(enabled=True, backend="fake", state_scope="request")
        )
        self.execute = AsyncMock(side_effect=self._execute)
        self.aclose = AsyncMock()
        self.published_files: tuple[SandboxPublishedFile, ...] = ()

    async def _execute(self, request: ExecuteRequest) -> ExecuteResult:
        self.requests.append(request)
        return ExecuteResult(
            request_id=request.request_id,
            status="ok",
            exit_code=0,
            stdout="",
            stderr="",
            result=42,
            error=None,
            duration_ms=1,
            backend="fake",
            environment="test",
            security_level="none",
            memory_peak_bytes=None,
            truncated=TruncationMetadata(),
            published_files=self.published_files,
        )


class FakeFileStore:
    def __init__(self, *, fail_upload_at: int | None = None) -> None:
        self.fail_upload_at = fail_upload_at
        self.uploaded: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def upload(self, **kwargs: Any) -> StoredFile:
        if self.fail_upload_at is not None and len(self.uploaded) + 1 == self.fail_upload_at:
            raise OSError("upload failed")
        self.uploaded.append(dict(kwargs))
        index = len(self.uploaded)
        return StoredFile(
            id=f"file-{index}",
            file_name=str(kwargs["file_name"]),
            file_size=len(kwargs["data"]),
            file_type=str(kwargs["file_type"]),
            mime=kwargs.get("mime"),
        )

    async def download(self, file_id: str, request_headers: dict[str, str]) -> bytes:
        raise AssertionError((file_id, request_headers))

    async def delete(self, file_id: str, request_headers: dict[str, str]) -> None:
        self.deleted.append(file_id)

    async def register_download(self, **kwargs: Any) -> str:
        raise AssertionError(kwargs)


class SandboxFactoryTests(unittest.TestCase):
    def test_disabled_returns_none(self) -> None:
        previous = OPTION.agent.sandbox_enabled
        OPTION.agent.sandbox_enabled = False
        try:
            self.assertIsNone(build_sandbox_backend())
        finally:
            OPTION.agent.sandbox_enabled = previous

    def test_unknown_backend_fails_at_startup(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "unknown-backend"
        try:
            with self.assertRaises(ValueError):
                build_sandbox_backend()
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend

    def test_missing_interpreter_fails_when_enabled(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "subprocess-dev"
        OPTION.agent.sandbox_python = "/nonexistent/deep-agent-sandbox-python"
        try:
            with self.assertRaises(SandboxInterpreterError):
                build_sandbox_backend()
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend
            OPTION.agent.sandbox_python = previous_python

    def test_subprocess_dev_uses_configured_interpreter(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "subprocess-dev"
        OPTION.agent.sandbox_python = sandbox_test_python()
        backend = None
        try:
            backend = build_sandbox_backend()
            self.assertIsNotNone(backend)
            assert backend is not None
            self.assertEqual(backend.python_executable, sandbox_test_python())
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend
            OPTION.agent.sandbox_python = previous_python


class SandboxBootstrapHardGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_profile_mismatch_aborts_bootstrap(self) -> None:
        from deep_agent.core.context import AppContext, reset_context
        from deep_agent.sandbox.profile import ProfileMismatchError, ProfileProbeResult

        previous = OPTION.agent.sandbox_enabled
        OPTION.agent.sandbox_enabled = True
        probe = ProfileProbeResult(
            profile_id="pydata",
            ok=False,
            missing_imports=("pandas",),
        )
        fake_backend = FakeSandboxBackend()
        fake_backend.python_executable = "/tmp/fake-sandbox-python"
        fake_bundle = MagicMock()
        fake_bundle.dialect.value = "sqlite"
        fake_bundle.stores = MagicMock()
        fake_bundle.stores.coordinator.interrupt_running = AsyncMock(return_value=0)
        fake_bundle.engine = MagicMock()
        fake_bundle.session_factory = MagicMock()

        try:
            with (
                patch(
                    "deep_agent.core.context.bootstrap_database",
                    new=AsyncMock(return_value=fake_bundle),
                ),
                patch(
                    "deep_agent.core.context.build_sandbox_backend",
                    return_value=fake_backend,
                ),
                patch(
                    "deep_agent.core.context.DefaultProfileVerifier.verify_strict",
                    new=AsyncMock(side_effect=ProfileMismatchError(probe)),
                ),
                patch("deep_agent.core.context.build_registry", return_value=ToolPackageRegistry()),
                patch("deep_agent.core.context.EventPersister"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    await AppContext.bootstrap()
            self.assertIn("pandas", str(ctx.exception))
            self.assertIn("/tmp/fake-sandbox-python", str(ctx.exception))
            fake_bundle.stores.coordinator.interrupt_running.assert_awaited_once_with()
        finally:
            OPTION.agent.sandbox_enabled = previous
            reset_context()

    async def test_disabled_sandbox_skips_profile_hard_gate(self) -> None:
        from deep_agent.core.context import AppContext, reset_context

        previous = OPTION.agent.sandbox_enabled
        OPTION.agent.sandbox_enabled = False
        fake_bundle = MagicMock()
        fake_bundle.dialect.value = "sqlite"
        fake_bundle.stores = MagicMock()
        fake_bundle.stores.coordinator.interrupt_running = AsyncMock(return_value=0)
        fake_bundle.engine = MagicMock()
        fake_bundle.session_factory = MagicMock()
        persister = MagicMock()
        persister.start = AsyncMock()
        ctx = None

        try:
            with (
                patch(
                    "deep_agent.core.context.bootstrap_database",
                    new=AsyncMock(return_value=fake_bundle),
                ),
                patch(
                    "deep_agent.core.context.DefaultProfileVerifier.verify_strict",
                    new=AsyncMock(),
                ) as verify,
                patch(
                    "deep_agent.core.context.build_registry",
                    return_value=ToolPackageRegistry(),
                ),
                patch(
                    "deep_agent.core.context.EventPersister",
                    return_value=persister,
                ),
            ):
                ctx = await AppContext.bootstrap()
            verify.assert_not_awaited()
            self.assertIsNone(ctx.sandbox_backend)
            self.assertIsNone(ctx.sandbox_profile)
            fake_bundle.stores.coordinator.interrupt_running.assert_awaited_once_with()
        finally:
            OPTION.agent.sandbox_enabled = previous
            reset_context()


class SandboxRegistryTests(unittest.TestCase):
    def test_registry_excludes_capability_when_backend_missing(self) -> None:
        registry = build_registry()
        self.assertEqual(
            {item.name for item in registry.get("builtin:general").tools},
            {"get_current_time"},
        )
        self.assertNotIn("builtin:sandbox", registry.codes())

    def test_registry_includes_capability_when_backend_present(self) -> None:
        registry = build_registry(sandbox_backend=FakeSandboxBackend())
        self.assertEqual(
            {item.name for item in registry.get("builtin:general").tools},
            {"get_current_time"},
        )
        self.assertEqual(
            {item.name for item in registry.get("builtin:sandbox").tools},
            {"execute_python"},
        )


class SandboxCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_publishes_full_large_result_alongside_explicit_preview(self) -> None:
        backend = SubprocessBackend(python_executable=sandbox_test_python())
        store = FakeFileStore()
        ctx = _context(file_store=store)
        package = build_python_sandbox_tool_package(backend, timeout_sec=30)
        try:
            tool = (await package.toolset.get_tools(ctx))["execute_python"]
            returned = await tool.call_func({"code": (
                "from deepagent_files import publish_path\n"
                "body = '长文' * 100000 + '完整末尾'\n"
                "target = publish_path('full-result.txt', mime='text/plain')\n"
                "with open(target, 'w', encoding='utf-8') as stream:\n"
                "    stream.write(body)\n"
                "body"
            )}, ctx)
            value = returned.return_value
            self.assertEqual(value["status"], "ok")
            self.assertTrue(value["truncated"]["result"])
            self.assertTrue(value["result"]["is_preview"])
            self.assertEqual(store.uploaded[0]["data"].decode(), "长文" * 100000 + "完整末尾")
            file_id = value["files"][0]["file_id"]
            self.assertIn(file_id, ctx.deps.session_file_artifacts)
            self.assertEqual(returned.metadata["generated_files"][0]["file_id"], file_id)
        finally:
            await backend.aclose()

    async def _tool(self, backend: FakeSandboxBackend):
        package = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        tools = await package.toolset.get_tools(_context())
        return tools["execute_python"], package

    async def test_capability_metadata(self) -> None:
        backend = FakeSandboxBackend()
        capability = build_python_sandbox_tool_package(backend, timeout_sec=30.0)

        self.assertEqual(capability.code, "builtin:sandbox")
        self.assertEqual(capability.category, "builtin")
        self.assertFalse(hasattr(capability, "default_enabled"))
        self.assertEqual(capability.source, "builtin")
        self.assertEqual(
            {item.name for item in capability.tools},
            {"execute_python"},
        )
        self.assertIn("发布转换产物", capability.tools[0].description)

    async def test_model_instructions_are_concise_and_include_fixed_example(self) -> None:
        backend = FakeSandboxBackend()
        capability = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        instruction_parts = await capability.toolset.get_instructions(_context())
        instructions = "\n".join(part.content for part in instruction_parts)

        self.assertIn("预装 numpy、pandas、pydantic", instructions)
        self.assertIn('values = inputs["values"]', instructions)
        self.assertIn('{"count": len(values), "total": sum(values)}', instructions)
        self.assertNotIn("stderr", instructions)
        self.assertNotIn("truncated", instructions)

    async def test_only_explicit_selection_loads_tool(self) -> None:
        backend = FakeSandboxBackend()
        capability = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        registry = ToolPackageRegistry()
        registry.register(capability)

        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets([]), [])
        self.assertEqual(registry.toolsets(["builtin:sandbox"]), [capability.toolset])

    async def test_tool_signature_exposes_code_inputs_and_files(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        schema = tool.tool_def.parameters_json_schema
        self.assertEqual(
            set(schema.get("properties", {}).keys()),
            {"code", "inputs", "files"},
        )
        description = tool.tool_def.description or ""
        self.assertIn("确实需要解析、转码或生成新文件", description)
        self.assertIn("load_file_data(url=...)", description)
        self.assertIn("仅要求发送/转交 WebP", description)

    async def test_user_none_is_rejected(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        with self.assertRaises(ModelRetry):
            await tool.call_func(
                {"code": "1 + 1"},
                _context(user=None),
            )
        backend.execute.assert_not_awaited()

    async def test_request_excludes_headers_tokens_attachments_and_db_state(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        await tool.call_func(
            {"code": "x = 1", "inputs": {"a": 1}},
            _context(
                headers={"authorization": "Bearer secret", "x-user-token": "tok"},
                attachment_bytes={"file-1": b"secret-bytes"},
            ),
        )

        self.assertEqual(len(backend.requests), 1)
        request = backend.requests[0]
        self.assertEqual(request.code, "x = 1")
        self.assertEqual(request.inputs, {"a": 1})
        self.assertEqual(request.timeout_ms, 30_000)
        payload: dict[str, Any] = request.model_dump()
        self.assertEqual(
            set(payload.keys()),
            {
                "request_id",
                "mode",
                "code",
                "arguments",
                "inputs",
                "files",
                "timeout_ms",
            },
        )
        self.assertEqual(payload["mode"], "expression")
        self.assertEqual(payload["arguments"], ())
        self.assertEqual(payload["files"], ())

    async def test_backend_result_is_tool_return_without_binary(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        result = await tool.call_func({"code": "21 * 2"}, _context())

        self.assertIsInstance(result, ToolReturn)
        assert isinstance(result, ToolReturn)
        self.assertEqual(result.return_value["status"], "ok")
        self.assertEqual(result.return_value["result"], 42)
        self.assertEqual(result.return_value["backend"], "fake")
        self.assertEqual(result.return_value["files"], [])
        self.assertEqual(result.metadata, {"generated_files": []})

    async def test_authorized_input_and_published_file_are_transferred_by_reference(self) -> None:
        backend = FakeSandboxBackend()
        backend.published_files = (
            SandboxPublishedFile(
                file_name="result.png",
                mime="image/png",
                byte_size=3,
                sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                data=b"abc",
            ),
        )
        store = FakeFileStore()
        tool, _ = await self._tool(backend)
        ctx = _context(attachment_bytes={"source-id": b"source"}, file_store=store)

        returned = await tool.call_func(
            {
                "code": "pass",
                "files": [{"file_id": "source-id", "path": "inputs/source.bin"}],
            },
            ctx,
        )

        self.assertIsInstance(returned, ToolReturn)
        assert isinstance(returned, ToolReturn)
        request = backend.requests[-1]
        self.assertEqual(request.files[0].file_id, "source-id")
        self.assertEqual(request.files[0].decoded(), b"source")
        refs = returned.return_value["files"]
        self.assertEqual(refs[0]["file_id"], "file-1")
        self.assertNotIn("data", refs[0])
        self.assertNotIn("path", refs[0])
        self.assertEqual(returned.metadata, {"generated_files": refs})
        self.assertEqual(ctx.deps.session_file_artifacts["file-1"], refs[0])

    async def test_tool_return_scrubs_sandbox_paths_but_keeps_file_references(self) -> None:
        backend = FakeSandboxBackend()
        published = SandboxPublishedFile(
            file_name="result.png",
            mime="image/png",
            byte_size=3,
            sha256=hashlib.sha256(b"abc").hexdigest(),
            data=b"abc",
        )
        backend.execute = AsyncMock(
            return_value=ExecuteResult(
                request_id="request-1",
                status="ok",
                exit_code=0,
                stdout=(
                    "read inputs/source.bin, wrote outputs/result.png at "
                    "/private/var/folders/demo/sandbox/result.png\n"
                ),
                stderr="",
                result={"input": "inputs/source.bin", "output": "outputs/result.png"},
                error=None,
                duration_ms=1,
                backend="fake",
                environment="test",
                security_level="none",
                memory_peak_bytes=None,
                truncated=TruncationMetadata(),
                published_files=(published,),
            )
        )
        store = FakeFileStore()
        tool, _ = await self._tool(backend)

        returned = await tool.call_func(
            {
                "code": "pass",
                "files": [{"file_id": "source-id", "path": "inputs/source.bin"}],
            },
            _context(attachment_bytes={"source-id": b"source"}, file_store=store),
        )

        assert isinstance(returned, ToolReturn)
        encoded = str(returned.return_value)
        self.assertNotIn("inputs/source.bin", encoded)
        self.assertNotIn("outputs/result.png", encoded)
        self.assertNotIn("/private/var/folders", encoded)
        self.assertIn("[sandbox-path]", encoded)
        self.assertEqual(returned.return_value["files"][0]["file_id"], "file-1")

    async def test_multiple_output_upload_failure_rolls_back(self) -> None:
        backend = FakeSandboxBackend()
        backend.published_files = tuple(
            SandboxPublishedFile(
                file_name=f"result-{index}.txt",
                mime="text/plain",
                byte_size=1,
                sha256=hashlib.sha256(bytes([index])).hexdigest(),
                data=bytes([index]),
            )
            for index in (1, 2)
        )
        store = FakeFileStore(fail_upload_at=2)
        tool, _ = await self._tool(backend)
        with self.assertRaises(ToolFailed):
            await tool.call_func({"code": "pass"}, _context(file_store=store))
        self.assertEqual(store.deleted, ["file-1"])

    async def test_upload_native_error_and_incomplete_cleanup_are_reported(self):
        backend = FakeSandboxBackend()
        backend.published_files = tuple(
            SandboxPublishedFile(file_name=f"output-{i}.html", mime="text/html", byte_size=1,
                                 sha256=hashlib.sha256(b"x").hexdigest(), data=b"x")
            for i in (1, 2)
        )
        store = FakeFileStore()
        store.upload = AsyncMock(side_effect=[
            StoredFile(id="first", file_name="output-1.html", file_size=1, file_type="DL"),
            APIStatusNotTrue("files/upload", "不支持该文件类型 html", "FORMAT_DENIED"),
        ])
        store.delete = AsyncMock(side_effect=OSError("storage unavailable"))
        ctx = _context(file_store=store)
        tool, _ = await self._tool(backend)
        with self.assertRaises(ToolFailed) as raised:
            await tool.call_func({"code": "pass"}, ctx)
        message = str(raised.exception)
        self.assertIn("不支持该文件类型 html", message)
        self.assertIn("FORMAT_DENIED", message)
        self.assertIn("storage unavailable", message)
        self.assertIn("部分本次产物清理失败", message)
        self.assertNotIn("已回滚", message)
        self.assertFalse(ctx.deps.session_file_artifacts)
        store.delete.assert_awaited_once_with("first", {**ctx.deps.request_headers, "user": ctx.deps.user})


if __name__ == "__main__":
    unittest.main()
