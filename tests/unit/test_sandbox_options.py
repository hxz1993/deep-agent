from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.options import OPTION
from deep_agent.sandbox import SubprocessBackend
from deep_agent.sandbox.profile import (
    DEFAULT_SANDBOX_PROFILE,
    ProfileProbeResult,
    load_sandbox_profile,
    probe_sandbox_profile,
    resolve_sandbox_profile,
)
from deep_agent.sandbox.subprocess_dev.rlimit import rlimit_as_supported
from deep_agent.sandbox.types import BackendHealth, ExecuteResult, TruncationMetadata
from tests.helpers.sandbox import sandbox_test_python
from tests.helpers.sqlite_stores import make_sqlite_stores


def _live_backend(*, max_concurrency: int = 2) -> SubprocessBackend:
    return SubprocessBackend(
        python_executable=sandbox_test_python(),
        max_concurrency=max_concurrency,
    )


class SandboxHealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._previous_enabled = OPTION.agent.sandbox_enabled
        stores, self._engine, session_factory = await make_sqlite_stores()
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.transport = ASGITransport(app=self.app)
        self._stores = stores
        self._session_factory = session_factory

    async def asyncTearDown(self) -> None:
        OPTION.agent.sandbox_enabled = self._previous_enabled
        try:
            await get_context().event_persister.aclose()
        except RuntimeError:
            pass
        reset_context()
        await self._engine.dispose()

    def _init_context(self, *, sandbox_backend=None, sandbox_profile=None) -> None:
        init_context(
            AppContext.for_testing(
                stores=self._stores,
                db_engine=self._engine,
                db_session_factory=self._session_factory,
                sandbox_backend=sandbox_backend,
                sandbox_profile=sandbox_profile,
            )
        )

    async def _get_health(self) -> dict:
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health/sandbox")
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_health_disabled_returns_minimal_payload(self) -> None:
        OPTION.agent.sandbox_enabled = False
        self._init_context(sandbox_backend=None)

        payload = await self._get_health()
        self.assertEqual(payload, {"enabled": False})

    async def test_health_subprocess_dev_reports_security_metadata(self) -> None:
        OPTION.agent.sandbox_enabled = True
        backend = _live_backend()
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = await probe_sandbox_profile(manifest, backend, timeout_ms=30_000)
        resolved = resolve_sandbox_profile(manifest, probe)
        self._init_context(sandbox_backend=backend, sandbox_profile=resolved)
        try:
            payload = await self._get_health()
        finally:
            await backend.aclose()

        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["backend"], "subprocess-dev")
        self.assertEqual(payload["state_scope"], "request")
        self.assertEqual(payload["security_level"], "none")
        self.assertEqual(payload["expected_profile"], DEFAULT_SANDBOX_PROFILE)
        self.assertTrue(payload["profile_ok"])
        self.assertIn("numpy", payload["package_names"])
        self.assertIn("pandas", payload["package_names"])
        self.assertIn("pydantic", payload["package_names"])
        self.assertNotIn("duckdb", payload["package_names"])
        self.assertEqual(payload["missing_packages"], [])
        self.assertTrue(payload["smoke_ok"])
        self.assertIsNotNone(payload.get("resource_limits"))
        limits = payload["resource_limits"]
        self.assertEqual(limits["memory_bytes"], 512 * 1024 * 1024)
        expected_enforcement = "rlimit_as" if rlimit_as_supported() else "none"
        expected_as = 1536 * 1024 * 1024 if rlimit_as_supported() else None
        self.assertEqual(limits["memory_enforcement"], expected_enforcement)
        self.assertEqual(limits["memory_as_bytes"], expected_as)
        self.assertEqual(limits["cpu_seconds"], 30)
        self.assertIsNone(limits["max_pids"])
        self.assertNotIn(".venv-sandbox", str(payload).lower())

    async def test_health_without_profile_reports_profile_not_ok(self) -> None:
        OPTION.agent.sandbox_enabled = True
        backend = _live_backend()
        self._init_context(sandbox_backend=backend)
        try:
            payload = await self._get_health()
        finally:
            await backend.aclose()

        self.assertFalse(payload["profile_ok"])
        self.assertEqual(payload["package_names"], [])
        self.assertEqual(payload["missing_packages"], [])

    async def test_health_does_not_expose_paths_or_environment(self) -> None:
        OPTION.agent.sandbox_enabled = True
        backend = _live_backend()
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = await probe_sandbox_profile(manifest, backend, timeout_ms=30_000)
        resolved = resolve_sandbox_profile(manifest, probe)
        self._init_context(sandbox_backend=backend, sandbox_profile=resolved)
        try:
            payload = await self._get_health()
        finally:
            await backend.aclose()

        serialized = str(payload).lower()
        for forbidden in (
            "pythonpath",
            "pythonhome",
            "cgroup",
            "/tmp/",
            "deep-agent-sandbox",
            "environment",
            "cwd",
            "work_dir",
        ):
            self.assertNotIn(forbidden, serialized)

    async def test_health_smoke_failure_reports_smoke_not_ok(self) -> None:
        OPTION.agent.sandbox_enabled = True
        backend = MagicMock()
        backend.health = AsyncMock(
            return_value=BackendHealth(
                enabled=True,
                backend="fake",
                state_scope="request",
                security_level="none",
            )
        )
        backend.execute = AsyncMock(
            return_value=ExecuteResult(
                request_id="smoke",
                status="error",
                exit_code=None,
                stdout="",
                stderr="",
                result=None,
                error=None,
                duration_ms=1,
                backend="fake",
                environment="test",
                security_level="none",
                memory_peak_bytes=None,
                truncated=TruncationMetadata(),
            )
        )
        self._init_context(sandbox_backend=backend)

        payload = await self._get_health()
        self.assertFalse(payload["smoke_ok"])
        backend.execute.assert_awaited_once()
        request = backend.execute.await_args.args[0]
        self.assertEqual(request.code, "1 + 1")
        self.assertEqual(request.inputs, {})

    async def test_health_reports_profile_mismatch(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = ProfileProbeResult(
            profile_id=manifest.id,
            ok=False,
            missing_imports=("pandas",),
        )
        resolved = resolve_sandbox_profile(manifest, probe)
        backend = _live_backend()
        init_context(
            AppContext.for_testing(
                stores=self._stores,
                db_engine=self._engine,
                db_session_factory=self._session_factory,
                sandbox_backend=backend,
                sandbox_profile=resolved,
            )
        )
        try:
            payload = await self._get_health()
        finally:
            await backend.aclose()

        self.assertEqual(payload["expected_profile"], DEFAULT_SANDBOX_PROFILE)
        self.assertFalse(payload["profile_ok"])
        self.assertEqual(payload["missing_packages"], ["pandas"])
        self.assertIn("numpy", payload["package_names"])
        self.assertNotIn("duckdb", payload["package_names"])

    async def test_health_smoke_does_not_log_code_or_result(self) -> None:
        OPTION.agent.sandbox_enabled = True
        backend = _live_backend()
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = await probe_sandbox_profile(manifest, backend, timeout_ms=30_000)
        resolved = resolve_sandbox_profile(manifest, probe)
        self._init_context(sandbox_backend=backend, sandbox_profile=resolved)
        try:
            with patch("deep_agent.api.v1.sandbox.logger") as logger:
                await self._get_health()
        finally:
            await backend.aclose()

        for call in logger.info.call_args_list + logger.debug.call_args_list:
            message = " ".join(str(part) for part in call.args)
            self.assertNotIn("1 + 1", message)
            self.assertNotIn(" result=2", message.lower())


if __name__ == "__main__":
    unittest.main()
