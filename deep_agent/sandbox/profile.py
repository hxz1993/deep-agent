"""Sandbox Profile manifest loading, probing, and model instruction formatting.

Stage B ``scripts/sandbox_probe/common.py`` should call ``DefaultProfileVerifier``
for ``checks.python_profile``. Stage C secure Backend may extend manifest schema
when isolation backend is selected.
"""

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


def format_profile_tool_instructions(resolved: ResolvedSandboxProfile) -> str:
    packages = ", ".join(resolved.package_display_names)
    notes = "\n".join(f"- {line}" for line in resolved.manifest.model_notes)
    return (
        "每次调用 execute_python 都会启动全新的 Python 执行环境，不保留变量、"
        "函数或 import。需要继续计算时，必须再次传入完整代码和 inputs。\n"
        f"当前 Profile（{resolved.manifest.id}）预装三方库：{packages}。"
        "未列出的三方库会 ModuleNotFoundError；不支持 pip install。标准库可用。\n"
        f"{notes}\n"
        "结构化返回值必须写在代码最后一行作为表达式，pandas DataFrame/Series 同样以表达式结尾，"
        "沙箱会把预览写入 result。"
    )


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
