"""Sandbox Profile manifest loading, probing, and model instruction formatting."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.types import ExecuteRequest

DEFAULT_SANDBOX_PROFILE = "pydata"

__all__ = [
    "DEFAULT_SANDBOX_PROFILE",
    "DefaultProfileVerifier",
    "ProfileMismatchError",
    "ProfileNotFoundError",
    "ProfilePackage",
    "ProfileProbeResult",
    "ProfileValidationError",
    "ProfileVerifier",
    "ResolvedSandboxProfile",
    "SandboxProfileManifest",
    "build_profile_probe_code",
    "format_python_tool_instructions",
    "format_profile_tool_instructions",
    "load_sandbox_profile",
    "probe_sandbox_profile",
    "resolve_sandbox_profile",
]


class ProfileNotFoundError(FileNotFoundError):
    pass


class ProfileValidationError(ValueError):
    pass


class ProfilePackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    import_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    version_note: str | None = None


class SandboxProfileManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = ""
    packages: tuple[ProfilePackage, ...]
    model_notes: tuple[str, ...] = ()


class ProfileProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    ok: bool
    missing_imports: tuple[str, ...] = ()


class ResolvedSandboxProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: SandboxProfileManifest
    probe: ProfileProbeResult

    @property
    def profile_ok(self) -> bool:
        return self.probe.ok

    @property
    def package_names(self) -> tuple[str, ...]:
        return tuple(item.import_name for item in self.manifest.packages)

    @property
    def package_display_names(self) -> tuple[str, ...]:
        return tuple(item.display_name for item in self.manifest.packages)


def _profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def load_sandbox_profile(profile_id: str) -> SandboxProfileManifest:
    normalized = profile_id.strip()
    if not normalized:
        raise ProfileNotFoundError("empty profile id")
    path = _profiles_dir() / f"{normalized}.json"
    if not path.is_file():
        raise ProfileNotFoundError(f"profile manifest not found: {normalized}")
    try:
        manifest = SandboxProfileManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ProfileValidationError(f"invalid profile manifest: {normalized}") from exc
    if manifest.id != normalized:
        raise ProfileValidationError(
            f"profile manifest id mismatch: file={normalized} id={manifest.id}"
        )
    return manifest


def build_profile_probe_code(import_names: tuple[str, ...]) -> str:
    lines = [
        "import importlib",
        "out = {}",
    ]
    for name in import_names:
        lines.append(
            f"try:\n"
            f"    importlib.import_module('{name}')\n"
            f"    out['{name}'] = True\n"
            f"except ImportError:\n"
            f"    out['{name}'] = False"
        )
    lines.append("out")
    return "\n".join(lines)


async def probe_sandbox_profile(
    manifest: SandboxProfileManifest,
    backend: PythonExecutionBackend,
    *,
    timeout_ms: int,
) -> ProfileProbeResult:
    names = tuple(item.import_name for item in manifest.packages)
    if not names:
        return ProfileProbeResult(profile_id=manifest.id, ok=True, missing_imports=())

    request = ExecuteRequest(
        request_id=uuid.uuid4().hex,
        code=build_profile_probe_code(names),
        inputs={},
        timeout_ms=timeout_ms,
    )
    result = await backend.execute(request)
    if result.status != "ok" or not isinstance(result.result, dict):
        return ProfileProbeResult(
            profile_id=manifest.id,
            ok=False,
            missing_imports=names,
        )

    missing = tuple(name for name in names if not bool(result.result.get(name)))
    return ProfileProbeResult(
        profile_id=manifest.id,
        ok=not missing,
        missing_imports=missing,
    )


def resolve_sandbox_profile(
    manifest: SandboxProfileManifest,
    probe: ProfileProbeResult,
) -> ResolvedSandboxProfile:
    return ResolvedSandboxProfile(manifest=manifest, probe=probe)


def format_python_tool_instructions(package_names: tuple[str, ...]) -> str:
    packages = "、".join(package_names) if package_names else "当前 Profile 声明的三方包"
    return (
        "在全新的 Python 环境中执行数据计算和文件处理。每次调用需提供完整 code 和 inputs。"
        f"预装 {packages}，常用标准库可用，不支持 pip install。"
        "可使用 requests/httpx 访问执行环境网络可达的 HTTP 服务，包括企业内网 API；网络权限由部署环境控制。"
        "读取 files 中的平台文件时使用 from deepagent_files import input_path；"
        "需要保留的输出必须使用 publish_path(file_name, mime=None) 取得写入路径，"
        "成功执行后宿主会上传并在 files 中返回 file_id。普通临时文件不会上传。"
        "请将正式结果作为最后一行表达式返回到 result；print 仅用于少量 stdout 诊断。"
        "优先返回 dict/list 等结构化结果，大表返回汇总或必要预览。"
        "result 超过512 KiB以及表格/数组超过10行或项时只返回标记为不完整的预览；"
        "完整数据必须在同一次执行中写入 publish_path 返回的路径，再根据 files/file_id 读取。"
        "不要仅为找回预览遗漏的数据重复执行有副作用的脚本。\n\n"
        "示例：\n"
        "```python\n"
        'values = inputs["values"]\n'
        'print(f"共 {len(values)} 项")\n'
        '{"count": len(values), "total": sum(values)}\n'
        "```\n\n"
        "文件转换示例：\n"
        "```python\n"
        "from deepagent_files import input_path, publish_path\n"
        'source = input_path(inputs["file_id"])\n'
        'target = publish_path("result.png", mime="image/png")\n'
        "# 读取 source 并将结果写入 target\n"
        '{"saved": True}\n'
        "```"
    )


def format_profile_tool_instructions(resolved: ResolvedSandboxProfile) -> str:
    return format_python_tool_instructions(resolved.package_display_names)


class ProfileMismatchError(RuntimeError):
    def __init__(self, probe: ProfileProbeResult) -> None:
        self.probe = probe
        super().__init__(
            f"sandbox profile {probe.profile_id} mismatch: missing={probe.missing_imports}"
        )


@runtime_checkable
class ProfileVerifier(Protocol):
    async def verify_strict(
        self,
        manifest: SandboxProfileManifest,
        backend: PythonExecutionBackend,
        *,
        timeout_ms: int,
    ) -> ProfileProbeResult: ...


class DefaultProfileVerifier:
    async def verify_strict(
        self,
        manifest: SandboxProfileManifest,
        backend: PythonExecutionBackend,
        *,
        timeout_ms: int,
    ) -> ProfileProbeResult:
        probe = await probe_sandbox_profile(manifest, backend, timeout_ms=timeout_ms)
        if not probe.ok:
            raise ProfileMismatchError(probe)
        return probe
