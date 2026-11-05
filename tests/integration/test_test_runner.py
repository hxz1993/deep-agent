"""Small isolated fixtures for test selection and result reporting."""
from __future__ import annotations

import contextlib
import ast
from collections import Counter
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers.test_selection import GROUP_FILES, SOURCE_GROUPS, changed_paths, dependent_tests, select_tests

_SPEC = importlib.util.spec_from_file_location(
    "_deepagent_test_runner", Path(__file__).resolve().parents[2] / "scripts/run_tests.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def spawn_probe():
    """A real spawn child reimports this module; importing the runner must not run tests."""
    return "spawn-safe"


class TestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TMP to delete-test-runner-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for path in ("tests/__init__.py", "tests/unit/__init__.py", "tests/integration/__init__.py"):
            self.write(path, "")
        self.write("tests/unit/test_provider_fixture.py", "")
        self.write("tests/unit/test_skill_fixture.py", "")
        self.write("tests/unit/test_unrelated_fixture.py", "")
        self.write("tests/frontend/console/fixture.test.mjs", "")
        groups = {"providers": ["tests/unit/test_provider_fixture.py"],
                  "files": ["tests/unit/test_skill_fixture.py"]}
        self.group_patch = patch("tests.helpers.test_selection.GROUP_FILES", groups)
        self.group_patch.start()
        self.addCleanup(self.group_patch.stop)

    def write(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout

    def init_git(self):
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Test Fixture")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture baseline")

    def test_domain_union_console_and_direct_test_are_selected(self):
        selected = select_tests(self.root, "affected", (
            "src/deep_agent/models/provider_registry.py", "src/deep_agent/skills/runtime.py",
            "tests/unit/test_unrelated_fixture.py", "console/chat.js"))
        self.assertFalse(selected.full)
        self.assertEqual(set(selected.groups), {"providers", "files"})
        self.assertEqual(len(selected.python_files), 3)
        self.assertEqual(len(selected.console_files), 1)
        self.assertTrue(any("Console" in reason for reason in selected.reasons))
        with patch("tests.helpers.test_selection.GROUP_FILES", {"providers": ["tests/unit/missing.py"]}):
            self.assertTrue(select_tests(self.root, "affected", ("src/deep_agent/models/provider_registry.py",)).full)
        with patch("tests.helpers.test_selection.GROUP_FILES", None):
            self.assertTrue(select_tests(self.root, "affected", ("src/deep_agent/models/provider_registry.py",)).full)
            self.assertTrue(select_tests(self.root, "all").full)
        selected = select_tests(self.root, "affected", (
            "src/deep_agent/builtin_skills/file-reading-verification/SKILL.md",))
        self.assertEqual(selected.groups, ("files",))
        self.assertEqual(selected.python_files, ("tests/unit/test_skill_fixture.py",))

    def test_production_imports_have_explicit_regression_groups(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIsInstance(GROUP_FILES, dict)
        missing = []
        for source in (root / "tests").rglob("test_*.py"):
            imported = set()
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                    imported.update(node.module + "." + alias.name for alias in node.names)
            for path, group in SOURCE_GROUPS.items():
                module = path.removeprefix("src/").removesuffix(".py").rstrip("/").replace("/", ".")
                if any(name == module or (path.endswith("/") and name.startswith(module + ".")) for name in imported):
                    relative = source.relative_to(root).as_posix()
                    if relative not in GROUP_FILES[group]:
                        missing.append((group, relative))
        self.assertEqual(missing, [], "新增或移动相关测试时，需同步显式领域清单")

    def test_transitive_helper_and_relative_import_users_are_included(self):
        self.write("tests/helpers/base.py", "VALUE = 1")
        self.write("tests/helpers/bridge.py", "from .base import VALUE")
        self.write("tests/unit/test_provider_fixture.py", "from tests.helpers import bridge")
        self.write("tests/unit/test_skill_fixture.py", "from .test_provider_fixture import anything")
        selected = select_tests(self.root, "affected", ("tests/helpers/base.py",))
        self.assertFalse(selected.full)
        self.assertEqual(set(selected.python_files), {
            "tests/unit/test_provider_fixture.py", "tests/unit/test_skill_fixture.py"})
        self.assertEqual(dependent_tests(self.root, {"tests/helpers/base.py"}, set(selected.python_files)),
                         set(selected.python_files))
        self.write("tests/helpers/unused.py", "")
        self.assertTrue(select_tests(self.root, "affected", (
            "tests/helpers/base.py", "tests/helpers/unused.py")).full)

    def test_unknown_shared_deleted_and_unresolvable_paths_fall_back(self):
        self.write("tests/helpers/unused.py", "")
        self.write("tests/helpers/broken.py", "this is not Python !")
        for path in ("src/deep_agent/new_feature.py", "src/deep_agent/db/models.py",
                     "src/deep_agent/agent/runner.py", "requirements.txt", "scripts/run_tests.py",
                     "tests/unit/__init__.py", "tests/integration/__init__.py",
                     "docs/design/evidence.schema.json", "docs/archive/Jenkinsfile.original",
                     "mcp_servers/customer/SKILL.md", "tests/fixtures/SKILL.md",
                     "tests/unit/deleted_test.py", "tests/helpers/unused.py", "tests/helpers/broken.py"):
            with self.subTest(path=path):
                selected = select_tests(self.root, "affected", (path,))
                self.assertTrue(selected.full)
                self.assertEqual(len(selected.python_files), 3)
                self.assertEqual(len(selected.console_files), 1)

    def test_quick_is_explicit_and_missing_entries_are_not_silently_removed(self):
        with patch("tests.helpers.test_selection.QUICK_FILES", ("tests/unit/test_provider_fixture.py",)):
            selected = select_tests(self.root, "quick")
            self.assertEqual(selected.python_files, ("tests/unit/test_provider_fixture.py",))
            self.assertEqual(len(selected.console_files), 1)
        with self.assertRaisesRegex(ValueError, "清单失效"):
            select_tests(self.root, "quick")
        selected = select_tests(self.root, "affected", ("docs/design/example.md",))
        self.assertFalse(selected.full)
        self.assertFalse(selected.python_files)
        self.assertFalse(selected.console_files)

    def test_git_changes_include_commit_staged_unstaged_untracked_rename_and_delete(self):
        self.write(".gitignore", "ignored/\n")
        self.write("delete.txt", "delete")
        self.write("rename old.txt", "rename")
        self.init_git()
        self.write("committed.txt", "committed")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture change")
        self.git("mv", "rename old.txt", "rename new.txt")
        self.git("rm", "delete.txt")
        self.write("staged.txt", "staged")
        self.git("add", "staged.txt")
        self.write("tests/unit/test_provider_fixture.py", "# unstaged")
        self.write("untracked 名字.txt", "untracked")
        self.write("ignored/private.txt", "ignore")
        changed = set(changed_paths(self.root, "HEAD~1"))
        self.assertEqual(changed, {"committed.txt", "rename old.txt", "rename new.txt", "delete.txt",
                                  "staged.txt", "tests/unit/test_provider_fixture.py", "untracked 名字.txt"})
        with self.assertRaisesRegex(ValueError, "Git 差异"):
            changed_paths(self.root, "not-a-ref")
        with self.assertRaisesRegex(ValueError, "Git 差异"):
            changed_paths(self.root, "--output=unexpected.txt")
        self.assertFalse((self.root / "unexpected.txt").exists())

    def test_real_unittest_retains_subtests_skips_functions_and_failure_status(self):
        class Cases(unittest.TestCase):
            def test_ok(self):
                for number in (1, 2):
                    with self.subTest(number=number):
                        self.assertGreater(number, 0)
            @unittest.skip("fixture skipped")
            def test_skip(self):
                pass
            def test_failure(self):
                with self.subTest(value="fixture"):
                    self.fail("fixture assertion")
            def test_error(self):
                raise RuntimeError("fixture error")
        functions = [unittest.FunctionTestCase(lambda: None, description=f"function {i}") for i in (1, 2)]
        # Different FunctionTestCase callables normally need distinct stable ids;
        # provide explicit names here, mirroring the repository's load_tests.
        for number, test in enumerate(functions):
            test.id = lambda number=number: f"fixture_function_{number}"
        suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Cases), *functions])
        with patch.object(unittest.TestLoader, "discover", return_value=suite):
            result = runner.run_python(self.root, (), full=True, stream=io.StringIO())
        self.assertFalse(result["successful"])
        self.assertEqual((result["run"], result["failures"], result["errors"], result["skips"]), (6, 1, 1, 1))
        self.assertEqual((result["subtests"], result["subtest_failures"], result["subtest_errors"]), (3, 1, 0))
        self.assertEqual(result["skip_reasons"], {"fixture skipped": 1})
        self.assertEqual(len(result["timings"]), 6)
        self.assertEqual(len(result["inventory"]), 6)
        self.assertEqual(Counter(test["status"] for test in result["tests"]),
                         {"passed": 3, "failed": 1, "error": 1, "skipped": 1})
        self.assertTrue(all(test["module"] == __name__ for test in result["tests"][-2:]))
        self.assertNotIn("fixture assertion", json.dumps(result))

    def test_duplicate_collection_is_an_error_and_does_not_execute_or_dedupe(self):
        from tests.helpers.channel_accounts import ChannelAccountsFixture
        from tests.helpers.channel_gateway import ChannelGatewayFixture
        from tests.helpers.session_list import SessionListFixture
        for fixture in (ChannelAccountsFixture, ChannelGatewayFixture, SessionListFixture):
            self.assertFalse(issubclass(fixture, unittest.TestCase))
            self.assertFalse(any(name.startswith("test_") for name in vars(fixture)))
        called = []
        class Cases(unittest.TestCase):
            def test_once(self):
                called.append(True)
        suite = unittest.TestSuite([Cases("test_once"), Cases("test_once")])
        with patch.object(unittest.TestLoader, "discover", return_value=suite):
            result = runner.run_python(self.root, (), full=True, stream=io.StringIO())
        self.assertFalse(result["successful"])
        self.assertEqual(result["run"], 0)
        self.assertEqual(result["collected"], 2)
        self.assertTrue(result["duplicate_ids"])
        self.assertFalse(called)

    def test_fixture_errors_skip_only_subtests_and_real_spawn_are_reported(self):
        class BrokenFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("fixture private detail")
            def test_unreachable(self):
                self.fail("unreachable")
        class SkipSubtest(unittest.TestCase):
            def test_partial(self):
                with self.subTest(number=1):
                    self.skipTest("subtest skip")
        suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(BrokenFixture),
                                   unittest.defaultTestLoader.loadTestsFromTestCase(SkipSubtest)])
        with patch.object(unittest.TestLoader, "discover", return_value=suite):
            report = runner.run_python(self.root, (), full=True, stream=io.StringIO())
        self.assertFalse(report["successful"])
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["tests"][0]["subtest_skips"], 1)
        self.assertNotEqual(report["tests"][0]["status"], "running")
        self.assertEqual(report["fixture_events"][0]["status"], "error")
        self.assertNotIn("fixture private detail", json.dumps(report))
        with multiprocessing.get_context("spawn").Pool(1) as pool:
            self.assertEqual(pool.apply_async(spawn_probe).get(timeout=15), "spawn-safe")

    def test_cli_list_exit_codes_and_safe_json_report(self):
        self.write(".gitignore", "/output/\n")
        self.init_git()
        output = "output/TMP to delete-test-runner/result.json"
        success = {"successful": True, "run": 1, "collected": 1, "failures": 0, "errors": 0,
                   "skips": 0, "timings": []}
        self.write("src/runner_child_probe.py", "VALUE = 42\n")
        previous_pythonpath = os.environ.get("PYTHONPATH")
        def python_with_child(*args, **kwargs):
            self.assertEqual(os.environ["PYTHONPATH"], os.pathsep.join((str(self.root / "src"), str(self.root))))
            child = subprocess.run([sys.executable, "-c", "from runner_child_probe import VALUE; assert VALUE == 42"],
                                   capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            return success
        with patch.object(runner, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            with patch.object(runner, "run_python") as python:
                self.assertEqual(runner.main(["--list", "--json-report", output]), 0)
                python.assert_not_called()
                self.assertFalse((self.root / output).exists())
            with patch.object(runner, "run_python", side_effect=python_with_child), \
                 patch.object(runner, "run_console", return_value={"successful": True}):
                self.assertEqual(runner.main(["--json-report", output, "--durations", "0"]), 0)
                report = json.loads((self.root / output).read_text())
                self.assertTrue(report["successful"])
                self.assertEqual(report["python"]["run"], 1)
            with patch.object(runner, "run_python", return_value={**success, "successful": False}), \
                 patch.object(runner, "run_console", return_value={"successful": True}):
                self.assertEqual(runner.main([]), 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(["--json-report", "source.json"]), 2)
        self.assertEqual(os.environ.get("PYTHONPATH"), previous_pythonpath)
        for path in ("source.json", "output/plain/result.json", "output/TMP to delete-test-runner/result.txt",
                     "output/TMP to delete-test-runner/../../result.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                runner.report_target(self.root, path)
        self.write("output/TMP to delete-test-runner/tracked.json", "{}")
        self.git("add", "-f", "output/TMP to delete-test-runner/tracked.json")
        with self.assertRaises(ValueError):
            runner.report_target(self.root, "output/TMP to delete-test-runner/tracked.json")
        (self.root / "output/TMP to delete-link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            runner.report_target(self.root, "output/TMP to delete-link/result.json")
