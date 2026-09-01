from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from deep_agent.sandbox.profile import (
    DEFAULT_SANDBOX_PROFILE,
    DefaultProfileVerifier,
    ProfileMismatchError,
    ProfileNotFoundError,
    ProfileProbeResult,
    ProfileValidationError,
    build_profile_probe_code,
    format_profile_tool_instructions,
    load_sandbox_profile,
    probe_sandbox_profile,
    resolve_sandbox_profile,
)
from deep_agent.sandbox.types import ExecuteRequest, ExecuteResult, TruncationMetadata


class FakeProbeBackend:
    def __init__(self, probe_result: dict[str, bool]) -> None:
        self.probe_result = probe_result
        self.requests: list[ExecuteRequest] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, request: ExecuteRequest) -> ExecuteResult:
        self.requests.append(request)
        return ExecuteResult(
            request_id=request.request_id,
            status="ok",
            exit_code=0,
            stdout="",
            stderr="",
            result=self.probe_result,
            error=None,
            duration_ms=1,
            backend="fake",
            environment="test",
            security_level="none",
            memory_peak_bytes=None,
            truncated=TruncationMetadata(),
        )


class SandboxProfileLoadTests(unittest.TestCase):
    def test_default_profile_constant_matches_manifest(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        self.assertEqual(manifest.id, DEFAULT_SANDBOX_PROFILE)
        self.assertEqual(manifest.id, "pydata")

    def test_load_pydata_profile(self) -> None:
        manifest = load_sandbox_profile("pydata")
        imports = {item.import_name for item in manifest.packages}
        self.assertIn("numpy", imports)
        self.assertIn("pandas", imports)
        self.assertIn("pydantic", imports)
        self.assertNotIn("duckdb", imports)
        numpy_pkg = next(p for p in manifest.packages if p.import_name == "numpy")
        self.assertEqual(numpy_pkg.version_note, ">=1.26,<2")
        pydantic_pkg = next(p for p in manifest.packages if p.import_name == "pydantic")
        self.assertEqual(pydantic_pkg.version_note, ">=2,<3")

    def test_missing_profile_raises(self) -> None:
        with self.assertRaises(ProfileNotFoundError):
            load_sandbox_profile("does-not-exist")

    def test_manifest_id_must_match_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles_dir = Path(tmp)
            (profiles_dir / "pydata.json").write_text(
                '{"id":"other","description":"","packages":[],"model_notes":[]}',
                encoding="utf-8",
            )
            with patch(
                "deep_agent.sandbox.profile._profiles_dir",
                return_value=profiles_dir,
            ):
                with self.assertRaises(ProfileValidationError):
                    load_sandbox_profile("pydata")


class SandboxProfileProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_code_lists_all_import_names(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        names = tuple(item.import_name for item in manifest.packages)
        code = build_profile_probe_code(names)
        self.assertIn("numpy", code)
        self.assertIn("pandas", code)
        self.assertIn("pydantic", code)
        self.assertNotIn("duckdb", code)
        self.assertTrue(code.strip().endswith("out"))

    async def test_probe_reports_missing_imports(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        package_state = {item.import_name: True for item in manifest.packages}
        package_state["pandas"] = False
        backend = FakeProbeBackend(package_state)
        probe = await probe_sandbox_profile(manifest, backend, timeout_ms=30_000)
        self.assertFalse(probe.ok)
        self.assertEqual(probe.missing_imports, ("pandas",))

        resolved = resolve_sandbox_profile(manifest, probe)
        self.assertFalse(resolved.profile_ok)
        self.assertIn("numpy", resolved.package_names)
        self.assertIn("pandas", resolved.package_names)
        self.assertIn("requests", resolved.package_names)


class ProfileVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_verifier_raises_on_missing(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        package_state = {item.import_name: True for item in manifest.packages}
        package_state["pandas"] = False
        backend = FakeProbeBackend(package_state)
        verifier = DefaultProfileVerifier()
        with self.assertRaises(ProfileMismatchError):
            await verifier.verify_strict(manifest, backend, timeout_ms=30_000)


class SandboxProfileInstructionsTests(unittest.TestCase):
    def test_instructions_list_allowed_packages(self) -> None:
        manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        probe = ProfileProbeResult(profile_id=manifest.id, ok=True, missing_imports=())
        resolved = resolve_sandbox_profile(manifest, probe)
        text = format_profile_tool_instructions(resolved)
        self.assertIn("numpy", text)
        self.assertIn("pandas", text)
        self.assertIn("pydantic", text)
        self.assertIn("requests", text)
        self.assertIn("publish_path", text)
        self.assertNotIn("duckdb", text)
        self.assertIn("pip install", text)
        self.assertIn("全新", text)
        self.assertIn('values = inputs["values"]', text)
        self.assertIn('print(f"共 {len(values)} 项")', text)
        self.assertIn('{"count": len(values), "total": sum(values)}', text)
        self.assertIn("最后一行表达式返回到 result", text)
        self.assertNotIn("stderr", text)
        self.assertNotIn("truncated", text)


if __name__ == "__main__":
    unittest.main()
