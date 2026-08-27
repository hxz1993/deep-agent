from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.schemas.skill import SkillPackageRecord
from deep_agent.skills.package import parse_skill_zip
from deep_agent.skills.runtime import (
    RUNTIME_ERROR,
    RUNTIME_READY,
    SkillRuntimeLibrary,
    resolve_skills_for_run,
)
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


class _Files:
    blobs: dict[str, bytes] = {}
    downloads = 0
    fail = False

    async def download(self, file_id: str) -> bytes:
        type(self).downloads += 1
        await asyncio.sleep(0)
        if type(self).fail:
            raise RuntimeError("platform unavailable")
        return type(self).blobs[file_id]


class _PlatformAPI:
    headers: list[dict | None] = []

    def __init__(self, header=None):
        self.header = header
        type(self).headers.append(header)
        self.file = _Files()


def _record(payload: bytes, *, file_id: str) -> SkillPackageRecord:
    parsed = parse_skill_zip(payload)
    try:
        return SkillPackageRecord(
            code=parsed.code,
            label=parsed.label,
            description=parsed.description,
            version=parsed.version,
            schema_version=parsed.schema_version,
            source="upload",
            platform_file_id=file_id,
            file_name=f"{parsed.code}.zip",
            file_size=len(payload),
            content_hash=parsed.content_hash,
            manifest=parsed.manifest,
            resource_index=parsed.resource_index,
            contains_scripts=parsed.contains_scripts,
        )
    finally:
        shutil.rmtree(parsed.package_root.parent, ignore_errors=True)


class SkillRuntimeLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.root = Path(tempfile.mkdtemp(prefix="skill-runtime-library-TMP_to_delete-"))
        self.runtime = SkillRuntimeLibrary(self.root)
        _Files.blobs = {}
        _Files.downloads = 0
        _Files.fail = False
        _PlatformAPI.headers = []
        self.platform_patch = patch(
            "deep_agent.files.storage.PlatformAPI",
            _PlatformAPI,
        )
        self.platform_patch.start()

    async def asyncTearDown(self) -> None:
        self.platform_patch.stop()
        shutil.rmtree(self.root, ignore_errors=True)
        await self.engine.dispose()

    async def _create(self, payload: bytes, *, file_id: str = "file-1"):
        _Files.blobs[file_id] = payload
        return await self.stores.skill_package.create(
            _record(payload, file_id=file_id)
        )

    async def test_one_current_directory_is_reused_and_replaced(self) -> None:
        original = skill_zip()
        row = await self._create(original)
        first = await self.runtime.materialize(row, payload=original)
        self.assertEqual(first.status, RUNTIME_READY)
        self.assertEqual(
            sorted(item.name for item in self.root.iterdir()),
            ["example-skill"],
        )

        resolved = await self.runtime.resolve(row, request_headers={})
        self.assertIsNotNone(resolved)
        self.assertEqual(_Files.downloads, 0)

        replacement = skill_zip(scripts=True)
        _Files.blobs["file-2"] = replacement
        updated = await self.stores.skill_package.replace_package(
            row.code,
            _record(replacement, file_id="file-2"),
        )
        assert updated is not None
        state = await self.runtime.materialize(
            updated,
            payload=replacement,
            force=True,
        )
        self.assertEqual(state.status, RUNTIME_READY)
        self.assertTrue((self.root / "example-skill" / "scripts" / "run.py").is_file())
        self.assertEqual(
            sorted(item.name for item in self.root.iterdir()),
            ["example-skill"],
        )

    async def test_startup_reconcile_cleans_orphans_and_downloads_once(self) -> None:
        payload = skill_zip()
        row = await self._create(payload)
        (self.root / "orphan-skill").mkdir(parents=True)

        await self.runtime.reconcile(self.stores)

        self.assertFalse((self.root / "orphan-skill").exists())
        self.assertTrue((self.root / row.code / "SKILL.md").is_file())
        self.assertEqual(_Files.downloads, 1)
        self.assertNotIn("space", _PlatformAPI.headers[0])
        self.assertEqual(self.runtime.state(row.code).status, RUNTIME_READY)

        restarted = SkillRuntimeLibrary(self.root)
        await restarted.reconcile(self.stores)
        self.assertEqual(_Files.downloads, 1)
        self.assertEqual(restarted.state(row.code).status, RUNTIME_READY)

    async def test_run_space_is_removed_from_fallback_download(self) -> None:
        payload = skill_zip()
        row = await self._create(payload)

        resolved = await self.runtime.resolve(
            row,
            request_headers={
                "app": "run-app",
                "space": "run-space",
                "language": "zh_CN",
                "x-deepfos-internal-key": "untrusted-value",
            },
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(_Files.downloads, 1)
        self.assertFalse(
            {"app", "space", "language"} & set(_PlatformAPI.headers[0])
        )
        self.assertNotEqual(
            _PlatformAPI.headers[0].get("x-deepfos-internal-key"),
            "untrusted-value",
        )

    async def test_platform_json_error_is_reported_as_missing_file(self) -> None:
        payload = skill_zip()
        row = await self._create(payload)
        _Files.blobs[row.platform_file_id] = (
            b'{"status":false,"data":null,"message":"file path not found"}'
        )

        state = await self.runtime.materialize(
            row,
            request_headers={"space": "run-space"},
        )

        self.assertEqual(state.status, RUNTIME_ERROR)
        self.assertEqual(state.error_code, "platform_file_missing")

    async def test_concurrent_missing_directory_uses_one_download(self) -> None:
        payload = skill_zip()
        row = await self._create(payload)

        first, second = await asyncio.gather(
            self.runtime.resolve(row, request_headers={}),
            self.runtime.resolve(row, request_headers={}),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(_Files.downloads, 1)

    async def test_failed_fallback_is_not_retried_on_every_run(self) -> None:
        payload = skill_zip()
        row = await self._create(payload)
        _Files.fail = True

        first = await resolve_skills_for_run(
            self.stores,
            [row.code],
            runtime=self.runtime,
            request_headers={},
        )
        second = await resolve_skills_for_run(
            self.stores,
            [row.code],
            runtime=self.runtime,
            request_headers={},
        )

        self.assertIsNone(first.capability)
        self.assertEqual(first.as_context()["unavailable"][0]["reason"], "download_failed")
        self.assertIsNone(second.capability)
        self.assertEqual(_Files.downloads, 1)
        self.assertEqual(self.runtime.state(row.code).status, RUNTIME_ERROR)

    async def test_uploaded_skills_share_one_harness_library_and_one_loader(self) -> None:
        rows = []
        for index, code in enumerate(("first-skill", "second-skill"), start=1):
            payload = skill_zip(name=code)
            rows.append(await self._create(payload, file_id=f"file-{index}"))
            await self.runtime.materialize(rows[-1], payload=payload)
        resolved = await resolve_skills_for_run(
            self.stores,
            [row.code for row in rows],
            runtime=self.runtime,
            request_headers={},
        )
        self.assertEqual(
            {item.library_root for item in resolved.items},
            {self.root.resolve()},
        )

        observed: list[AgentInfo] = []

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            observed.append(info)
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            capabilities=[resolved.capability],
        )
        await agent.run("hello", deps=AgentDeps(session_id="skills"))

        self.assertIn("first-skill", observed[0].instructions or "")
        self.assertIn("second-skill", observed[0].instructions or "")
        self.assertEqual(
            [tool.name for tool in observed[0].function_tools].count("load_capability"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
