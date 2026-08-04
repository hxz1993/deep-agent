from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.options import OPTION
from deep_agent.sandbox.factory import SandboxInterpreterError, build_sandbox_backend
from deep_agent.sandbox.toolset import build_python_sandbox_tool_package
from deep_agent.sandbox.types import (
    ExecuteRequest,
    ExecuteResult,
    TruncationMetadata,
)
from deep_agent.schemas.attachment import AttachmentRef
from tests.helpers.sandbox import sandbox_test_python


def _context(
    *,
    user: str | None = "user-1",
    headers: dict[str, str] | None = None,
    attachment_bytes: dict[str, bytes] | None = None,
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
            attachment_bytes=attachment_bytes or {},
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
        )


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
        fake_bundle.engine = MagicMock()
        fake_bundle.session_factory = MagicMock()
        persister = MagicMock()
        persister.start = AsyncMock()

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
    async def _tool(self, backend: FakeSandboxBackend):
        package = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        tools = await package.toolset.get_tools(_context())
        return tools["execute_python"], package

    async def test_capability_metadata(self) -> None:
        backend = FakeSandboxBackend()
        capability = build_python_sandbox_tool_package(backend, timeout_sec=30.0)

        self.assertEqual(capability.code, "builtin:sandbox")
        self.assertEqual(capability.category, "builtin")
        self.assertFalse(capability.default_enabled)
        self.assertEqual(capability.source, "builtin")
        self.assertEqual({item.name for item in capability.tools}, {"execute_python"})

    async def test_only_explicit_selection_loads_tool(self) -> None:
        backend = FakeSandboxBackend()
        capability = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        registry = ToolPackageRegistry()
        registry.register(capability)

        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets([]), [])
        self.assertEqual(registry.toolsets(["builtin:sandbox"]), [capability.toolset])

    async def test_tool_signature_only_code_and_inputs(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        schema = tool.tool_def.parameters_json_schema
        self.assertEqual(set(schema.get("properties", {}).keys()), {"code", "inputs"})

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
        self.assertEqual(set(payload.keys()), {"request_id", "code", "inputs", "timeout_ms"})

    async def test_backend_result_is_plain_dict(self) -> None:
        backend = FakeSandboxBackend()
        tool, _ = await self._tool(backend)
        result = await tool.call_func({"code": "21 * 2"}, _context())

        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 42)
        self.assertEqual(result["backend"], "fake")


if __name__ == "__main__":
    unittest.main()
