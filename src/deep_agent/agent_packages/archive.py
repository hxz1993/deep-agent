from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate
from deep_agent.schemas.agent import AgentOutputConfig
from deep_agent.skills.package import parse_skill_zip

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_FILES = 2048
# Product resources belong to the installation feature, independent of cwd.
BUILTIN_PACKAGES_ROOT = Path(__file__).resolve().parent / 'builtin'
CODE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


class PackageError(ValueError):
    pass


class PackageConflict(PackageError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExtensiblePackageModel(BaseModel):
    """Ignore safe future package data; installation operation DTOs stay strict."""
    model_config = ConfigDict(extra='ignore')


class Manifest(ExtensiblePackageModel):
    format: Literal['deepagent-agent-package']
    schema_version: Literal[1]
    exported_from: str = Field(min_length=1, max_length=64)


class ModelReference(ExtensiblePackageModel):
    name: str = Field(min_length=1, max_length=191)
    provider_type: str = Field(min_length=1, max_length=64)


class PackageOutputConfig(AgentOutputConfig):
    model_config = ConfigDict(extra='ignore', from_attributes=True)


class PackageAgent(ExtensiblePackageModel):
    name: str = Field(min_length=1, max_length=191)
    prompt: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    model_reference: ModelReference | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=256)
    skills: list[str] = Field(default_factory=list, max_length=256)
    structured_output_enabled: bool = Field(default=False, strict=True)
    allow_text_output: bool = Field(default=False, strict=True)
    output_config: PackageOutputConfig | None = None

    @model_validator(mode='after')
    def consistent_output(self):
        if self.structured_output_enabled != (self.output_config is not None):
            raise ValueError('开启输出必须携带定义，关闭输出的安装包不得携带定义草稿')
        if self.allow_text_output and not self.structured_output_enabled:
            raise ValueError('关闭输出的安装包不得携带纯文本输出草稿')
        return self

    @model_validator(mode='before')
    @classmethod
    def discard_legacy_ui_tool_selection(cls, value):
        if isinstance(value, dict) and 'ui_tool_selection' in value:
            return {key: item for key, item in value.items() if key != 'ui_tool_selection'}
        return value

    @field_validator('name', 'prompt')
    @classmethod
    def text_required(cls, v):
        if not v.strip():
            raise ValueError('must not be blank')
        return v

    @field_validator('capabilities', 'skills')
    @classmethod
    def unique_codes(cls, values):
        if len(set(values)) != len(values) or any(not x or len(x) > 191 for x in values):
            raise ValueError('resource codes must be unique and non-empty')
        return values


class PackageUiTool(UiToolCreate):
    model_config = ConfigDict(extra='ignore')


class PackageUi(ExtensiblePackageModel):
    code: str
    label: str
    description: str | None = None
    tools: list[PackageUiTool] = Field(max_length=256)

    def validated(self):
        UiToolPackageCreate.model_validate(self.model_dump(exclude={'tools'}))
        if any(t.package_code != self.code for t in self.tools):
            raise PackageError('UI 工具与工具包编码不一致')
        return self


class InstallOptions(StrictModel):
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    name: str = Field(min_length=1, max_length=191)
    model_id: str | None = None
    mode: Literal['create', 'overwrite'] = 'create'
    target_agent_id: str | None = None

    @model_validator(mode='after')
    def validate_target(self):
        if (self.mode == 'overwrite') != bool(self.target_agent_id):
            raise ValueError('覆盖安装必须选择目标 Agent，新建安装不能指定目标')
        return self

    @field_validator('model_id')
    @classmethod
    def model_reference_id(cls, value):
        if value is not None and not value.strip():
            raise ValueError('未配置模型请使用 null')
        return value

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Agent 名称不能为空')
        return value.strip()


@dataclass
class Package:
    manifest: Manifest
    agent: PackageAgent
    ui: list[PackageUi]
    skills: dict[str, tuple[bytes, dict]]
    digest: str


def prompt_version(prompt: str) -> str | None:
    """Read an explicit V-number from the prompt title, never the service version."""
    heading = prompt.lstrip().splitlines()[0] if prompt.strip() else ''
    if not heading.startswith('# '):
        return None
    match = re.search(r'(?:\bV|[-_]v)([0-9]+(?:\.[0-9]+)*)\s*$', heading, re.IGNORECASE)
    return f'V{match.group(1)}' if match else None


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def json_read(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackageError('JSON 包含重复字段')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(PackageError('JSON 数字无效')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PackageError('安装包 JSON 无效') from exc


def read_zip(raw: bytes, budget: list[int]) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = {}
            seen = set()
            for info in archive.infolist():
                name = info.filename
                path = PurePosixPath(name)
                if (info.orig_filename != name or name in seen or '\\' in name or path.is_absolute() or '..' in path.parts
                        or not path.parts or ':' in name or '\x00' in name
                        or str(path) != name.rstrip('/') or info.flag_bits & 1
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    raise PackageError('ZIP 包含重复文件、非法路径、链接或加密内容')
                seen.add(name)
                budget[0] += 1
                budget[1] += info.file_size
                if budget[0] > MAX_PACKAGE_FILES or budget[1] > MAX_EXPANDED_BYTES:
                    raise PackageError('安装包解压大小或文件数量超限')
                if not info.is_dir():
                    files[name] = archive.read(info)
            return files
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise PackageError('无法读取安装包 ZIP，文件损坏或压缩格式不支持') from exc


def parse_package(raw: bytes) -> Package:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PackageError('安装包超过 50 MiB')
    budget = [0, 0]
    files = read_zip(raw, budget)
    required = {'manifest.json', 'agent.json', 'ui-tools.json'}
    if not required.issubset(files):
        raise PackageError('缺少 manifest.json、agent.json 或 ui-tools.json')
    manifest_data = json_read(files['manifest.json'])
    if not isinstance(manifest_data, dict) or type(manifest_data.get('schema_version')) is not int or manifest_data['schema_version'] != 1:
        raise PackageError('不支持的安装包格式版本，当前支持 v1')
    manifest = Manifest.model_validate(manifest_data)
    agent = PackageAgent.model_validate(json_read(files['agent.json']))
    ui_data = json_read(files['ui-tools.json'])
    if not isinstance(ui_data, list) or len(ui_data) > 256:
        raise PackageError('UI 工具包列表无效')
    ui = [PackageUi.model_validate(p).validated() for p in ui_data]
    if len({p.code for p in ui}) != len(ui) or len({t.name for p in ui for t in p.tools}) != sum(len(p.tools) for p in ui):
        raise PackageError('UI 工具包或工具名称重复')
    if any(p.code not in agent.capabilities for p in ui):
        raise PackageError('安装包包含未关联的 UI 工具包')
    if any(not c.startswith(('builtin:', 'ui:', 'mcp:')) for c in agent.capabilities):
        raise PackageError('无法识别的能力引用')
    skills = {}
    for name, payload in files.items():
        if not (name.startswith('skills/') and name.endswith('.zip') and len(PurePosixPath(name).parts) == 2):
            continue
        read_zip(payload, budget)
        parsed = parse_skill_zip(payload)
        try:
            if name != f'skills/{parsed.code}.zip' or parsed.code not in agent.skills:
                raise PackageError('Skill 文件名、编码或 Agent 引用不一致')
            skills[parsed.code] = (payload, {
                'code': parsed.code, 'label': parsed.label, 'description': parsed.description,
                'version': parsed.version, 'schema_version': parsed.schema_version,
                'source': 'upload', 'content_hash': parsed.content_hash,
                'manifest': parsed.manifest, 'resource_index': parsed.resource_index,
                'contains_scripts': parsed.contains_scripts,
            })
        finally:
            shutil.rmtree(parsed.package_root.parent, ignore_errors=True)
    return Package(manifest, agent, ui, skills, digest({n: hashlib.sha256(v).hexdigest() for n, v in files.items()}))


def build_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    result = output.getvalue()
    if len(result) > MAX_PACKAGE_BYTES:
        raise PackageError('导出安装包超过 50 MiB')
    return result


def read_builtin(code: str, root: Path | None = None) -> bytes:
    root = root or BUILTIN_PACKAGES_ROOT
    if not CODE.fullmatch(code):
        raise PackageError('无效的自带安装包编码')
    package_file = root / f'{code}.zip'
    if package_file.is_symlink():
        raise PackageError('自带安装包不能是符号链接')
    if not package_file.is_file():
        raise PackageError('自带安装包不存在')
    if package_file.stat().st_size > MAX_PACKAGE_BYTES:
        raise PackageError('自带安装包超过 50 MiB')
    with package_file.open('rb') as stream:
        raw = stream.read(MAX_PACKAGE_BYTES + 1)
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PackageError('自带安装包超过 50 MiB')
    return raw
