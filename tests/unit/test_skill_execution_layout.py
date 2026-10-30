from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.types import ExecuteRequest, MAX_CODE_BYTES, SandboxInputFile
from tests.helpers.sandbox import sandbox_test_python


def script_request(bundle: dict[str, str], *, entry: str = "scripts/report.py", **kwargs) -> ExecuteRequest:
    return ExecuteRequest(
        request_id="original-layout", mode="script", code="", script_entry=entry,
        skill_files=tuple(SandboxInputFile.from_bytes(name, data.encode()) for name, data in bundle.items()),
        **kwargs,
    )


class SkillExecutionLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend = SubprocessBackend(python_executable=sandbox_test_python(), max_concurrency=1)
        self.addAsyncCleanup(self.backend.aclose)

    async def test_original_entry_package_and_sibling_imports_publish_real_file(self) -> None:
        files = {
            "SKILL.md": "# test",
            "scripts/__init__.py": "PACKAGE_VALUE = 7\n",
            "scripts/helpers/__init__.py": "from .maths import total\n",
            "scripts/helpers/maths.py": "def total(values): return sum(values)\n",
            "scripts/sibling.py": "LABEL = 'sibling'\n",
            ".env": "DEMO=example\n",
            "assets/template.txt": "template",
            "scripts/report.py": (
                "import json, os, sys\nfrom pathlib import Path\n"
                "from . import PACKAGE_VALUE\nfrom .helpers import total\nfrom sibling import LABEL\n"
                "from deepagent_files import input_path, publish_path\n"
                "root = Path(__file__).resolve().parents[1]\n"
                "values = json.loads(Path('inputs.json').read_text())['values']\n"
                "text = Path(input_path('file-1')).read_text()\n"
                "output = Path('outputs/report.txt'); output.write_text(text + ':' + str(total(values)))\n"
                "publish_path('report.txt', mime='text/plain')\n"
                "Path('outputs/result.json').write_text(json.dumps({"
                "'total': total(values), 'package': __package__, 'main': __name__, "
                "'entry': str(Path(__file__).relative_to(Path.cwd())), "
                "'argv': sys.argv, 'label': LABEL, 'package_value': PACKAGE_VALUE, "
                "'env_file': root.joinpath('.env').read_text().strip(), "
                "'injected': os.getenv('DEMO'), 'template': root.joinpath('assets/template.txt').read_text(), "
                "'old_entry_exists': Path('main.py').exists()}))\n"
            ),
        }
        result = await self.backend.execute(script_request(files, inputs={"values": [1, 2, 3]},
            arguments=("--report",), files=(SandboxInputFile.from_bytes("inputs/input.txt", b"real", file_id="file-1"),)))
        self.assertEqual(result.status, "ok", result.error)
        self.assertEqual(result.result["total"], 6)
        self.assertEqual(result.result["package"], "scripts")
        self.assertEqual(result.result["main"], "__main__")
        self.assertEqual(result.result["entry"], "skill/scripts/report.py")
        self.assertTrue(result.result["argv"][0].endswith("skill/scripts/report.py"))
        self.assertEqual(result.result["argv"][1:], ["--report"])
        self.assertEqual(result.result["label"], "sibling")
        self.assertEqual(result.result["package_value"], 7)
        self.assertEqual(result.result["env_file"], "DEMO=example")
        self.assertIsNone(result.result["injected"])
        self.assertEqual(result.result["template"], "template")
        self.assertFalse(result.result["old_entry_exists"])
        self.assertEqual(len(result.published_files), 1)
        self.assertEqual(result.published_files[0].data, b"real:6")

    async def test_namespace_nested_package_and_root_entry(self) -> None:
        for entry, package in (("tools/nested/entry.py", "tools.nested"), ("entry.py", "")):
            with self.subTest(entry=entry):
                source = "import json\nfrom pathlib import Path\nPath('outputs/result.json').write_text(json.dumps({'package': __package__}))\n"
                result = await self.backend.execute(script_request({entry: source}, entry=entry))
                self.assertEqual(result.status, "ok", result.error)
                self.assertEqual(result.result, {"package": package})

    async def test_entry_filename_does_not_have_to_be_an_import_identifier(self) -> None:
        for filename in ("report-v2.py", "report.v2.py", "report.PY"):
            with self.subTest(filename=filename):
                source = ("import json\nfrom pathlib import Path\nfrom .helper import VALUE\n"
                          "Path('outputs/result.json').write_text(json.dumps({'value': VALUE, 'name': __name__}))\n")
                result = await self.backend.execute(script_request({
                    f"scripts/{filename}": source, "scripts/helper.py": "VALUE = 42\n",
                }, entry=f"scripts/{filename}"))
                self.assertEqual(result.status, "ok", result.error)
                self.assertEqual(result.result, {"value": 42, "name": "__main__"})

    async def test_package_initializer_entry_executes_only_once(self) -> None:
        source = (
            "import json\nfrom pathlib import Path\n"
            "counter=Path('outputs/counter.txt')\n"
            "count=int(counter.read_text())+1 if counter.exists() else 1\n"
            "counter.write_text(str(count))\n"
            "Path('outputs/result.json').write_text(json.dumps({'count':count,'package':__package__,'name':__name__}))\n"
        )
        result = await self.backend.execute(script_request({"scripts/__init__.py": source}, entry="scripts/__init__.py"))
        self.assertEqual(result.status, "ok", result.error)
        self.assertEqual(result.result, {"count": 1, "package": "scripts", "name": "__main__"})

    async def test_missing_module_fails_and_next_execution_is_clean(self) -> None:
        result = await self.backend.execute(script_request({"scripts/report.py": "from .missing import value\n"}))
        self.assertEqual(result.status, "error")
        self.assertIn("ModuleNotFoundError", result.error.message)
        result = await self.backend.execute(ExecuteRequest(request_id="next", code="40 + 2"))
        self.assertEqual(result.result, 42)

    async def test_timeout_and_cancellation_reap_process_and_remove_bundle(self) -> None:
        original = self.backend._spawn_worker
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                workers = []
                spawned = asyncio.Event()

                async def track(directory, request):
                    worker = await original(directory, request)
                    workers.append((Path(directory), worker))
                    spawned.set()
                    return worker

                task = None
                try:
                    with patch.object(self.backend, "_spawn_worker", side_effect=track):
                        task = asyncio.create_task(self.backend.execute(script_request({
                            "scripts/report.py": "import time\ntime.sleep(30)\n",
                        }, timeout_ms=1000 if not cancelled else 10000)))
                        await asyncio.wait_for(spawned.wait(), timeout=5)
                        if cancelled:
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                        else:
                            result = await task
                            self.assertEqual(result.error.code, "TIMEOUT")
                    self.assertTrue(workers)
                    for directory, worker in workers:
                        self.assertIsNotNone(worker.returncode)
                        self.assertFalse(directory.exists())
                    self.assertFalse(self.backend.semaphore_locked())
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    for _, worker in workers:
                        if worker.returncode is None:
                            await self.backend._terminate_process_group(worker)

    def test_bundle_is_materialized_once_and_removed_from_worker_frame(self) -> None:
        request = script_request({"scripts/report.py": "pass", ".env": "DEMO=value"}, inputs={"x": 1})
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-layout-") as temporary:
            root = Path(temporary)
            prepared = self.backend._prepare_work_dir(root, request)
            self.assertEqual(root.joinpath("skill/scripts/report.py").read_text(), "pass")
            self.assertEqual(root.joinpath("skill/.env").read_text(), "DEMO=value")
            self.assertFalse(root.joinpath("main.py").exists())
            self.assertEqual(prepared.skill_files, ())
            self.assertEqual(prepared.files, ())
            self.assertEqual(prepared.script_entry, "scripts/report.py")
            ExecuteRequest.model_validate(prepared.model_dump(mode="json"))

    def test_entry_bundle_and_attachment_validation(self) -> None:
        valid = {"scripts/report.py": "pass"}
        for entry in ("../bad.py", "/bad.py", "scripts/report.txt"):
            with self.subTest(entry=entry), self.assertRaises(ValidationError):
                script_request(valid, entry=entry)
        with self.assertRaises(ValidationError):
            script_request(valid, files=(SandboxInputFile.from_bytes("skill/.env", b"overwritten"),))
        with self.assertRaises(ValidationError):
            script_request({"scripts/report.py": "x" * (MAX_CODE_BYTES + 1)})
        with self.assertRaises(ValidationError):
            script_request({f"module{n}.py": "pass" for n in range(257)}, entry="module0.py")
        with self.assertRaises(ValidationError):
            ExecuteRequest(request_id="expression", code="1", script_entry="entry.py")
        with self.assertRaises(ValidationError):
            ExecuteRequest(request_id="old-layout", mode="script", code="pass")
        with self.assertRaises(ValidationError):
            ExecuteRequest(request_id="duplicates", mode="script", code="", script_entry="scripts/check.py",
                skill_files=(SandboxInputFile.from_bytes("scripts/check.py", b"pass"),) * 2)
        with self.assertRaises(ValidationError):
            script_request({"scripts/report.py": "pass", "assets/large.bin": "x" * (20 * 1024 * 1024)})
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-layout-") as temporary:
            with self.assertRaises(ValueError):
                self.backend._prepare_work_dir(Path(temporary), ExecuteRequest(
                    request_id="missing", mode="script", code="", script_entry="scripts/report.py"))


if __name__ == "__main__":
    unittest.main()
