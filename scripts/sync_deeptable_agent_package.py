"""Build the shipped spreadsheet Agent ZIP from its checked-in frontend assets.

Uses the frontend's own delivery filter and the backend's real package parser;
never installs configuration or copies source tests into a product package.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deep_agent.agent_packages.archive import PackageAgent, PackageUi, parse_package
from sync_console_g2_theme import OUTPUT as CONSOLE_THEME, render_theme


def deterministic_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return output.getvalue()


def build_from_source(source_repo: Path) -> tuple[bytes, dict]:
    source_repo = source_repo.resolve(strict=True)
    asset_path = "src/pages/sheet/use/components/aiPanel/agentAssets"
    builder_path = "scripts/prepare-agent-assets.mjs"
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", asset_path, builder_path],
        cwd=source_repo, text=True,
    )
    if dirty:
        raise ValueError("来源配置或整理器有未提交改动，请先冻结源版本")
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_repo, text=True,
    ).strip()
    program = """
      import {pathToFileURL} from 'node:url';
      const {loadAssets} = await import(pathToFileURL(process.argv[2]).href);
      const files = await loadAssets();
      process.stdout.write(JSON.stringify([...files].map(([name, data]) =>
        [name.replaceAll('\\\\', '/'), data.toString('base64')])));
    """
    delivered = json.loads(subprocess.check_output(
        ["node", "--input-type=module", "-e", program, "deepagent-package-sync", str(source_repo / builder_path)],
        cwd=source_repo, text=True,
    ))
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source_repo, text=True).strip() != source_commit or subprocess.check_output(
        ["git", "status", "--porcelain", "--", asset_path, builder_path], cwd=source_repo, text=True,
    ):
        raise ValueError("读取期间来源配置已变化，未生成混合版本安装包")
    files = {name: base64.b64decode(content, validate=True) for name, content in delivered}
    if len(files) != len(delivered):
        raise ValueError("来源交付文件存在重复路径")
    # Package models apply the same retired-code normalization as install/export.
    agent = PackageAgent.model_validate(json.loads(files.pop("agent.json")))
    ui = [PackageUi.model_validate(item).validated() for item in json.loads(files.pop("ui-tools.json"))]
    package_files = {
        "manifest.json": files.pop("manifest.json"),
        "agent.json": json.dumps(agent.model_dump(exclude_defaults=True), ensure_ascii=False, indent=2).encode(),
        "ui-tools.json": json.dumps([item.model_dump() for item in ui], ensure_ascii=False, indent=2).encode(),
    }
    skill_files: dict[str, dict[str, bytes]] = {}
    for name, content in files.items():
        path = PurePosixPath(name)
        if len(path.parts) < 3 or path.parts[0] != "skills" or path.parts[1] not in agent.skills:
            raise ValueError(f"来源交付路径无效：{name}")
        skill_files.setdefault(path.parts[1], {})[PurePosixPath(*path.parts[2:]).as_posix()] = content
    for code, contents in skill_files.items():
        package_files[f"skills/{code}.zip"] = deterministic_zip(contents)
    raw = deterministic_zip(package_files)
    parsed = parse_package(raw)
    if parsed.agent != agent or parsed.ui != ui:
        raise ValueError("安装包解析后配置与来源不一致")
    return raw, {
        "source_commit": source_commit,
        "delivered_files": len(delivered),
        "package_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "prompt_sha256": hashlib.sha256(agent.prompt.encode()).hexdigest(),
        "skills": {code: metadata["version"] for code, (_, metadata) in sorted(parsed.skills.items())},
        "capabilities": agent.capabilities,
        "model_reference": agent.model_reference.model_dump() if agent.model_reference else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="原子替换预制包；缺省只核对")
    args = parser.parse_args()
    raw, report = build_from_source(args.source_repo)
    target = ROOT / "src/deep_agent/agent_packages/builtin/deeptable-assistant.zip"
    if args.write:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix="TMP to delete-deeptable-", suffix=".zip", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(raw)
            os.replace(temporary, target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    elif target.read_bytes() != raw:
        raise SystemExit("预制包与来源不一致；核对来源后使用 --write 更新")
    theme = render_theme(target)
    if args.write:
        CONSOLE_THEME.write_text(theme, encoding="utf-8")
    elif not CONSOLE_THEME.exists() or CONSOLE_THEME.read_text("utf-8") != theme:
        raise SystemExit("Console 图表主题与预制 Skill 不一致；运行 scripts/sync_console_g2_theme.py 更新")
    report["action"] = "written" if args.write else "verified"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
