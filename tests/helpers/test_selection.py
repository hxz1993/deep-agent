"""Conservative, explicit test selection; no product imports or third-party dependencies."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import subprocess


# Reviewed pure logic only: no database, HTTP client, or real child process.
QUICK_FILES = (
    "tests/unit/test_agent_ids.py",
    "tests/unit/test_diagnostics_text.py",
    "tests/unit/test_forward_headers.py",
    "tests/unit/test_output_correction_messages.py",
    "tests/unit/test_reading_budget.py",
    "tests/unit/test_sandbox_framing.py",
    "tests/unit/test_sandbox_serialization.py",
    "tests/unit/test_structured_output_feedback.py",
    "tests/unit/test_web_search_settings.py",
)

# A source directory is opted in only with its cross-boundary regression group.
# New/unmapped product paths deliberately fall back to the complete suite.
SOURCE_GROUPS = {
    "src/deep_agent/developer_resources/": "developer_resources",
    "src/deep_agent/api/v1/developer_resources.py": "developer_resources",
    "scripts/build_developer_resources.py": "developer_resources",
    "scripts/sync_console_g2_theme.py": "console_theme",
    "src/deep_agent/models/": "providers",
    "src/deep_agent/skills/": "files",
    "src/deep_agent/builtin_skills/": "files",
    "src/deep_agent/files/": "files",
    "src/deep_agent/documents/": "files",
    "src/deep_agent/sandbox/": "files",
    "src/deep_agent/web/": "web",
    "src/deep_agent/channels/": "channels",
    **{f"src/deep_agent/services/{name}.py": "channels" for name in (
        "channel_registration", "channel_gateway_client", "channel_profiles",
        "wecom_profiles", "wecom_webhook", "wecom_registration", "wecom_client",
        "wecom_app_format", "feishu_registration", "feishu_permissions",
        "dingtalk_registration",
    )},
}
try:
    GROUP_FILES = json.loads(Path(__file__).with_name("test_groups.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    # A missing/broken selection manifest must not disable complete discovery.
    GROUP_FILES = None
RUNNER_PATHS = {
    "scripts/run_tests.py", "tests/helpers/test_selection.py", "tests/helpers/test_groups.json",
    "tests/integration/test_test_runner.py",
}


@dataclass(frozen=True)
class Selection:
    python_files: tuple[str, ...]
    console_files: tuple[str, ...]
    reasons: tuple[str, ...]
    full: bool = False
    groups: tuple[str, ...] = ()


def python_files(root: Path) -> tuple[str, ...]:
    """Mirror unittest's package recursion, without importing test modules."""
    found = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        ancestors = path.relative_to(root / "tests").parents
        if all((root / "tests" / ancestor / "__init__.py").is_file() for ancestor in ancestors):
            found.append(path.relative_to(root).as_posix())
    return tuple(found)


def console_files(root: Path) -> tuple[str, ...]:
    return tuple(path.relative_to(root).as_posix() for path in sorted(
        (root / "tests/frontend/console").glob("*.test.mjs")))


def changed_paths(root: Path, base: str) -> tuple[str, ...]:
    def git(*args: str) -> bytes:
        process = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=15)
        if process.returncode:
            raise ValueError("无法读取 Git 差异，请检查 --base 和仓库状态")
        return process.stdout
    # git diff <base> compares the base commit with the working tree, including
    # staged and unstaged edits. -z preserves whitespace/non-ASCII path names.
    commit = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode("ascii").strip()
    fields = git("diff", "--name-status", "-z", "--find-renames", commit, "--").split(b"\0")
    paths = set()
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii")
        index += 1
        count = 2 if status.startswith(("R", "C")) else 1
        for _ in range(count):
            paths.add(fields[index].decode("utf-8", errors="surrogateescape"))
            index += 1
    paths.update(value.decode("utf-8", errors="surrogateescape") for value in
                 git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if value)
    return tuple(sorted(paths))


def _module(path: str) -> str:
    name = path.removesuffix(".py").replace("/", ".")
    return name.removesuffix(".__init__")


def test_import_graph(root: Path) -> dict[str, set[str]]:
    """Test/helper Python imports, including relative and local function imports."""
    modules = {_module(path.relative_to(root).as_posix()): path.relative_to(root).as_posix()
               for path in (root / "tests").rglob("*.py")}
    graph: dict[str, set[str]] = {}
    for module, relative in modules.items():
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        dependencies = set()
        package = module if relative.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parts = package.split(".")
                    prefix = ".".join(parts[:len(parts) - node.level + 1] + ([prefix] if prefix else []))
                imports = [prefix, *(prefix + "." + alias.name for alias in node.names)]
            else:
                continue
            for name in imports:
                if name in modules:
                    dependencies.add(modules[name])
        graph[relative] = dependencies
    return graph


def dependent_tests(root: Path, changes: set[str], available: set[str]) -> set[str]:
    graph = test_import_graph(root)
    reached = set(changes)
    while True:
        added = {path for path, dependencies in graph.items() if dependencies & reached} - reached
        if not added:
            return reached & available
        reached.update(added)


def select_tests(root: Path, suite: str, changes: tuple[str, ...] = ()) -> Selection:
    backend, frontend = python_files(root), console_files(root)
    def full(reason: str) -> Selection:
        return Selection(backend, frontend, (reason,), full=True)
    if suite == "all":
        return full("完整回归")
    if suite == "quick":
        missing = sorted(set(QUICK_FILES) - set(backend))
        if missing:
            raise ValueError("快速组清单失效: " + ", ".join(missing))
        return Selection(QUICK_FILES, frontend, ("显式纯逻辑快速组 + Console 全量",))
    if suite != "affected":
        raise ValueError("未知测试组: " + suite)
    selected: set[str] = set()
    groups: set[str] = set()
    reasons = []
    use_console = False
    changed_test_python: set[str] = set()
    available = set(backend)
    for raw in sorted(set(changes)):
        path = PurePosixPath(raw).as_posix()
        if path.startswith("/") or ".." in PurePosixPath(path).parts:
            return full("路径无法安全分类，回退完整回归")
        if path in RUNNER_PATHS:
            return full(f"测试入口变更: {path}")
        if path.startswith("tests/") and PurePosixPath(path).name == "__init__.py":
            return full(f"测试包初始化变更: {path}")
        if path.startswith("console/") or path.startswith("tests/frontend/console/"):
            if path != "console/AGENTS.md":
                use_console = True
                reasons.append(f"Console 全量: {path}")
            continue
        if path in available:
            selected.add(path)
            changed_test_python.add(path)
            reasons.append(f"测试文件及其使用者: {path}")
            continue
        if path.startswith("tests/") and path.endswith(".py"):
            if not (root / path).is_file():
                return full(f"删除的测试或辅助模块: {path}")
            changed_test_python.add(path)
            reasons.append(f"测试辅助模块及其使用者: {path}")
            continue
        matched = [group for prefix, group in SOURCE_GROUPS.items()
                   if prefix.endswith("/") and path.startswith(prefix)]
        matched += [group for exact, group in SOURCE_GROUPS.items() if not exact.endswith("/") and path == exact]
        if matched:
            groups.update(matched)
            reasons.append(f"领域回归 {','.join(sorted(set(matched)))}: {path}")
            continue
        if (path.startswith("docs/") and path.endswith((".md", ".rst"))) or path in {
            "README.md", "AGENTS.md", "CHANGELOG.md", "tests/README.md", "tests/AGENTS.md",
            "tests/unit/README.md", "tests/integration/README.md",
        }:
            reasons.append(f"文档，无自动运行组: {path}")
            continue
        return full(f"共享入口、依赖、数据库或未映射路径: {path}")
    try:
        if changed_test_python:
            for changed in sorted(changed_test_python):
                users = dependent_tests(root, {changed}, available)
                # An unused helper/fixture may be read dynamically; do not silently skip.
                if not users:
                    return full(f"辅助模块使用者无法静态确定，回退完整回归: {changed}")
                selected.update(users)
    except (SyntaxError, UnicodeError, OSError):
        return full("无法解析测试依赖，回退完整回归")
    for group in groups:
        entries = GROUP_FILES.get(group, ()) if isinstance(GROUP_FILES, dict) else ()
        if not isinstance(entries, list) or any(not isinstance(path, str) for path in entries):
            return full(f"领域 {group} 回归清单失效，回退完整回归")
        candidates = set(entries)
        if not entries or len(candidates) != len(entries) or candidates - available:
            return full(f"领域 {group} 回归清单失效，回退完整回归")
        selected.update(candidates)
    return Selection(tuple(sorted(selected)), frontend if use_console else (),
                     tuple(reasons or ["无代码改动"]), groups=tuple(sorted(groups)))
