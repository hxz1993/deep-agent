#!/usr/bin/env python3
"""Run complete or conservatively selected unittest/Node suites without extra packages."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Match the documented production import boundary, with the repository included
# for canonical tests.* imports; never propagate this into a Sandbox Worker.
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tests.helpers.test_selection import changed_paths, select_tests  # noqa: E402


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []
        self.tests = []
        self.fixture_events = []
        self._records = {}
        self.subtests = self.subtest_failures = self.subtest_errors = 0
        self._started = {}

    def startTest(self, test):
        self._started[id(test)] = time.perf_counter()
        record = {"id": test.id(), "module": type(test).__module__,
                  "class": type(test).__qualname__, "method": getattr(test, "_testMethodName", None),
                  "status": "running", "subtests": 0, "subtest_failures": 0,
                  "subtest_errors": 0, "subtest_skips": 0}
        if isinstance(test, unittest.FunctionTestCase):
            record["module"] = test._testFunc.__module__
            record["method"] = test._testFunc.__name__
        self._records[id(test)] = record
        self.tests.append(record)
        super().startTest(test)

    def stopTest(self, test):
        seconds = time.perf_counter() - self._started.pop(id(test))
        self.timings.append({"id": test.id(), "seconds": seconds})
        self._records[id(test)]["seconds"] = seconds
        if self._records[id(test)]["status"] == "running":
            self._records[id(test)]["status"] = (
                "passed_with_skips" if self._records[id(test)]["subtest_skips"] else "passed")
        super().stopTest(test)

    def _status(self, test, status):
        record = self._records.get(id(test))
        if record is not None:
            record["status"] = status
        else:
            self.fixture_events.append({"id": test.id(), "status": status})

    def addSuccess(self, test):
        self._status(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._status(test, "failed")
        super().addFailure(test, err)

    def addError(self, test, err):
        self._status(test, "error")
        super().addError(test, err)

    def addSkip(self, test, reason):
        parent = getattr(test, "test_case", None)
        if parent is not None and id(parent) in self._records:
            self._records[id(parent)]["subtest_skips"] += 1
        else:
            self._status(test, "skipped")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._status(test, "expected_failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._status(test, "unexpected_success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        self.subtests += 1
        record = self._records[id(test)]
        record["subtests"] += 1
        if err is not None:
            if issubclass(err[0], test.failureException):
                self.subtest_failures += 1
                record["subtest_failures"] += 1
                if record["status"] != "error":
                    record["status"] = "failed"
            else:
                self.subtest_errors += 1
                record["subtest_errors"] += 1
                record["status"] = "error"
        super().addSubTest(test, subtest, err)


def run_python(root: Path, files: tuple[str, ...], *, full: bool, stream=None) -> dict:
    start = time.perf_counter()
    loader = unittest.TestLoader()
    if full:
        suite = loader.discover(str(root / "tests"), pattern="test_*.py", top_level_dir=str(root))
    else:
        names = [path.removesuffix(".py").replace("/", ".") for path in files]
        suite = loader.loadTestsFromNames(names)
    collection = time.perf_counter() - start
    cases = list(flatten(suite))
    duplicate_ids = {name: count for name, count in Counter(test.id() for test in cases).items() if count > 1}
    base = {"collection_seconds": collection, "collected": len(cases),
            "inventory": [test.id() for test in cases], "duplicate_ids": duplicate_ids}
    if duplicate_ids:
        # Do not silently hide test-import mistakes by dropping test instances.
        return {**base, "successful": False, "collection_errors": ["重复测试 ID，请修复 TestCase 导入"],
                "run": 0, "failures": 0, "errors": len(duplicate_ids), "skips": 0,
                "execution_seconds": 0, "timings": []}
    execution_start = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=1, resultclass=TimedResult, stream=stream).run(suite)
    return {**base, "successful": result.wasSuccessful(),
            "collection_errors": [test.id() for test in cases if type(test).__name__ == "_FailedTest"],
            "run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "subtests": result.subtests, "subtest_failures": result.subtest_failures,
            "subtest_errors": result.subtest_errors,
            "skip_reasons": dict(Counter(reason for _, reason in result.skipped)),
            "execution_seconds": time.perf_counter() - execution_start,
            "timings": result.timings, "tests": result.tests, "fixture_events": result.fixture_events}


def run_console(root: Path, files: tuple[str, ...]) -> dict:
    if not files:
        return {"selected": False, "successful": True, "run": 0, "execution_seconds": 0}
    started = time.perf_counter()
    counts = {}
    process = subprocess.Popen(["node", "--test", "--test-reporter=tap", *files], cwd=root,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=(os.name == "posix"))
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            match = re.fullmatch(r"# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*", line)
            if match:
                counts[match[1]] = int(match[2])
        returncode = process.wait()
    finally:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()
    return {"selected": True, "successful": returncode == 0, "returncode": returncode,
            "run": counts.get("tests", 0), "passed": counts.get("pass", 0),
            "failures": counts.get("fail", 0), "cancelled": counts.get("cancelled", 0),
            "skips": counts.get("skipped", 0), "todo": counts.get("todo", 0),
            "execution_seconds": time.perf_counter() - started}


def report_target(root: Path, value: str) -> Path:
    """Reports are opt-in and restricted to ignored output/TMP... directories."""
    target = Path(value)
    if not target.is_absolute():
        target = root / target
    relative = target.relative_to(root) if target.is_relative_to(root) else None
    if relative is None or ".." in relative.parts or len(relative.parts) < 3 or relative.parts[0] != "output":
        raise ValueError("JSON 报告必须位于 output/TMP to delete.../ 目录")
    label = relative.parts[1].lower().replace("_", " ").replace("-", " ")
    if not label.startswith("tmp to delete") or target.suffix.lower() != ".json":
        raise ValueError("报告目录必须标记 TMP to delete，文件后缀必须为 .json")
    if any((root / Path(*relative.parts[:index])).is_symlink() for index in range(1, len(relative.parts) + 1)):
        raise ValueError("JSON 报告路径不能包含符号链接")
    if not target.resolve().is_relative_to((root / "output").resolve()):
        raise ValueError("JSON 报告不能越过 output 目录")
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative.as_posix()],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative.as_posix()])
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise ValueError("JSON 报告路径必须被 Git 忽略，且不能覆盖已跟踪文件")
    return target


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "quick", "affected"), default="all")
    parser.add_argument("--base", default="HEAD", help="affected 模式的 Git 比较基线")
    parser.add_argument("--list", action="store_true", help="只显示选测文件和原因，不导入或执行测试")
    parser.add_argument("--durations", type=int, default=20, help="最慢用例数量，0 关闭")
    parser.add_argument("--json-report", metavar="PATH", help="可选：写入 output/TMP to delete.../*.json")
    args = parser.parse_args(argv)
    if args.durations < 0:
        parser.error("--durations 不能为负数")
    started = time.perf_counter()
    try:
        target = report_target(ROOT, args.json_report) if args.json_report else None
        changes = changed_paths(ROOT, args.base) if args.suite == "affected" else ()
        selection = select_tests(ROOT, args.suite, changes)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"测试入口错误: {error}", file=sys.stderr)
        return 2
    print(f"suite={args.suite} full={selection.full} Python={len(selection.python_files)} modules Console={len(selection.console_files)} files")
    for reason in selection.reasons:
        print(f"- {reason}")
    if args.list:
        for path in (*selection.python_files, *selection.console_files):
            print(path)
        return 0
    report = {"suite": args.suite, "base": args.base if args.suite == "affected" else None,
              "selection": {"full": selection.full, "groups": selection.groups,
                            "reasons": selection.reasons, "python_files": selection.python_files,
                            "console_files": selection.console_files}}
    try:
        report["python"] = run_python(ROOT, selection.python_files, full=selection.full)
        report["console"] = run_console(ROOT, selection.console_files)
    except (OSError, subprocess.SubprocessError) as error:
        report["entry_error"] = type(error).__name__
        print(f"测试执行器错误: {type(error).__name__}", file=sys.stderr)
    except KeyboardInterrupt:
        report["interrupted"] = True
        print("测试已中断", file=sys.stderr)
    report["wall_seconds"] = time.perf_counter() - started
    report["successful"] = (report.get("python", {}).get("successful", False)
                            and report.get("console", {}).get("successful", False))
    python = report.get("python", {})
    print(f"Python: collected={python.get('collected', 0)} run={python.get('run', 0)} failures={python.get('failures', 0)} errors={python.get('errors', 0)} skips={python.get('skips', 0)} collection={python.get('collection_seconds', 0):.3f}s execution={python.get('execution_seconds', 0):.3f}s")
    for error in python.get("collection_errors", ()):
        print(error, file=sys.stderr)
    if python.get("duplicate_ids"):
        print("重复测试 ID: " + ", ".join(python["duplicate_ids"]), file=sys.stderr)
    if args.durations:
        print(f"最慢 {args.durations} 项（包含初始化与清理）:")
        for item in sorted(python.get("timings", ()), key=lambda row: row["seconds"], reverse=True)[:args.durations]:
            print(f"{item['seconds']:.3f}s {item['id']}")
    print(f"总耗时 {report['wall_seconds']:.3f}s，{'通过' if report['successful'] else '未通过'}")
    if target is not None:
        target = report_target(ROOT, args.json_report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(f"JSON 报告: {target.relative_to(ROOT)}")
    return 0 if report["successful"] else 130 if report.get("interrupted") else 1


def main(argv=None) -> int:
    previous = Path.cwd()
    previous_pythonpath = os.environ.get("PYTHONPATH")
    try:
        os.chdir(ROOT)
        # Tests that spawn Python must see the same canonical source/test roots.
        # Sandbox Workers still construct their own restricted environment.
        os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        return _main(argv)
    finally:
        os.chdir(previous)
        if previous_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous_pythonpath


if __name__ == "__main__":
    raise SystemExit(main())
