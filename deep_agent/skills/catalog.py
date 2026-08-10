from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from deep_agent.skills.package import ParsedSkillPackage, inspect_skill_directory


BUILTIN_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "builtin_skills"


@dataclass(frozen=True)
class SkillDescriptor:
    code: str
    label: str
    description: str
    version: str | None
    schema_version: str
    source: str
    enabled: bool
    manifest: dict
    resource_index: list[dict]
    contains_scripts: bool
    package_root: Path


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _builtin_descriptor(path: Path) -> SkillDescriptor:
    parsed: ParsedSkillPackage = inspect_skill_directory(
        path,
        content_hash=_directory_hash(path),
    )
    return SkillDescriptor(
        code=parsed.code,
        label=parsed.label,
        description=parsed.description,
        version=parsed.version,
        schema_version=parsed.schema_version,
        source="builtin",
        enabled=True,
        manifest=parsed.manifest,
        resource_index=parsed.resource_index,
        contains_scripts=parsed.contains_scripts,
        package_root=path,
    )


def list_builtin_skills() -> list[SkillDescriptor]:
    if not BUILTIN_SKILLS_ROOT.is_dir():
        return []
    return [
        _builtin_descriptor(path)
        for path in sorted(BUILTIN_SKILLS_ROOT.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    ]


def get_builtin_skill(code: str) -> SkillDescriptor | None:
    return next((item for item in list_builtin_skills() if item.code == code), None)
