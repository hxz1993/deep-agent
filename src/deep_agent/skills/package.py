from __future__ import annotations

import hashlib
import io
import mimetypes
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_FILES = 256
MAX_DEPTH = 8
MAX_MODEL_TEXT_BYTES = 2 * 1024 * 1024
SKILL_SCHEMA_VERSION = "1.0"
READABLE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".html", ".xml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TEMPLATE_SUFFIXES = {".docx", ".xlsx", ".pptx", ".pdf"}


def is_env_resource(path: str | Path) -> bool:
    target = Path(path)
    return target.name == ".env" or target.suffix.lower() == ".env"


class SkillPackageError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSkillPackage:
    code: str
    label: str
    description: str
    version: str | None
    schema_version: str
    content_hash: str
    manifest: dict[str, Any]
    resource_index: list[dict[str, Any]]
    contains_scripts: bool
    package_root: Path


def _safe_yaml_mapping(text: str, *, source: str) -> dict[str, Any]:
    class UniqueKeyLoader(yaml.BaseLoader):
        def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
            keys: set[str] = set()
            for key_node, _ in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    raise SkillPackageError(f"{source} contains a non-scalar YAML key")
                if key_node.value in keys:
                    raise SkillPackageError(f"{source} contains duplicate key {key_node.value!r}")
                keys.add(key_node.value)
            return super().construct_mapping(node, deep=deep)

    try:
        value = yaml.load(text, Loader=UniqueKeyLoader)
    except (yaml.YAMLError, SkillPackageError) as exc:
        raise SkillPackageError(f"Invalid YAML in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise SkillPackageError(f"{source} must contain a YAML mapping")
    return {str(key): item for key, item in value.items()}


def _normalize_skill_name(value: str, *, source: str) -> str:
    name = unicodedata.normalize("NFKC", value.strip())
    if (
        not name
        or len(name) > 64
        or name != name.lower()
        or name.startswith("-")
        or name.endswith("-")
        or "--" in name
        or not all(char.isalnum() or char == "-" for char in name)
    ):
        raise SkillPackageError(
            f"Invalid Skill code {value!r} in {source}; use lowercase letters, numbers and single hyphens"
        )
    return name


def _read_skill_frontmatter(
    path: Path,
    *,
    require_directory_match: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillPackageError(f"SKILL.md must be valid UTF-8: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SkillPackageError("SKILL.md must start with YAML frontmatter delimited by ---")
    try:
        closing = lines[1:].index("---") + 1
    except ValueError as exc:
        raise SkillPackageError("SKILL.md has unclosed YAML frontmatter") from exc
    frontmatter = _safe_yaml_mapping("\n".join(lines[1:closing]), source="SKILL.md")
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        raise SkillPackageError("SKILL.md frontmatter requires a non-empty description")
    name_value = str(frontmatter.get("name") or path.parent.name)
    name = _normalize_skill_name(name_value, source="SKILL.md")
    if require_directory_match and name != unicodedata.normalize("NFKC", path.parent.name):
        raise SkillPackageError("SKILL.md name must match the package directory name")
    return name, description, frontmatter


def _archive_root(names: list[PurePosixPath]) -> PurePosixPath:
    files = [name for name in names if name.name]
    if PurePosixPath("SKILL.md") in files:
        return PurePosixPath(".")
    top = {name.parts[0] for name in files if name.parts}
    skill_roots = sorted(
        name for name in top if PurePosixPath(name) / "SKILL.md" in files
    )
    if len(skill_roots) > 1:
        raise SkillPackageError(
            "ZIP contains multiple Skills "
            f"({', '.join(skill_roots)}); split the Library into one ZIP per Skill"
        )
    if len(top) == 1:
        root = PurePosixPath(next(iter(top)))
        if root / "SKILL.md" in files:
            return root
    raise SkillPackageError("ZIP must contain SKILL.md at its root or inside one wrapper directory")


def _validate_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if info.flag_bits & 0x1:
        raise SkillPackageError(f"Encrypted ZIP entries are not allowed: {info.filename}")
    raw = info.filename.replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise SkillPackageError(f"Unsafe ZIP path: {info.filename}")
    if len(path.parts) > MAX_DEPTH + 1:
        raise SkillPackageError(f"ZIP path exceeds {MAX_DEPTH} directory levels: {info.filename}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise SkillPackageError(f"Symbolic links are not allowed: {info.filename}")
    return path


def _resource_entry(path: Path, relative: str, *, category: str) -> dict[str, Any]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    is_text = suffix in READABLE_SUFFIXES or is_env_resource(path)
    readable = category != "script" and (
        (is_text and len(data) <= MAX_MODEL_TEXT_BYTES)
        or suffix in {".docx", ".xlsx", ".pptx", ".pdf"}
    )
    if is_text and len(data) > MAX_MODEL_TEXT_BYTES:
        raise SkillPackageError(
            f"Model-readable resource exceeds {MAX_MODEL_TEXT_BYTES} bytes: {relative}"
        )
    mime = (
        "text/plain" if is_env_resource(path)
        else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return {
        "path": relative,
        "category": category,
        "mime": mime,
        "size": len(data),
        "content_hash": hashlib.sha256(data).hexdigest(),
        "readable": readable,
    }


def inspect_skill_directory(
    package_root: Path,
    *,
    content_hash: str,
    require_directory_match: bool = True,
) -> ParsedSkillPackage:
    skill_path = package_root / "SKILL.md"
    if not skill_path.is_file():
        raise SkillPackageError("SKILL.md is required")
    if skill_path.stat().st_size > MAX_MODEL_TEXT_BYTES:
        raise SkillPackageError(
            f"SKILL.md exceeds {MAX_MODEL_TEXT_BYTES} bytes"
        )
    code, description, frontmatter = _read_skill_frontmatter(
        skill_path,
        require_directory_match=require_directory_match,
    )

    manifest_path = package_root / "manifest.yaml"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = _safe_yaml_mapping(manifest_path.read_text("utf-8"), source="manifest.yaml")
    manifest_code = str(manifest.get("code") or code)
    if _normalize_skill_name(manifest_code, source="manifest.yaml") != code:
        raise SkillPackageError("manifest.yaml code must match SKILL.md name")
    version = str(manifest.get("version") or "").strip() or None
    schema_version = str(manifest.get("schema_version") or SKILL_SCHEMA_VERSION).strip()
    if schema_version != SKILL_SCHEMA_VERSION:
        raise SkillPackageError(
            f"Unsupported Skill schema_version {schema_version!r}; expected {SKILL_SCHEMA_VERSION}"
        )
    label = str(manifest.get("label") or frontmatter.get("label") or code).strip()
    if not label:
        label = code

    resources: list[dict[str, Any]] = []
    contains_scripts = False
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_root).as_posix()
        top = relative.split("/", 1)[0]
        if relative == ".env":
            resources.append(_resource_entry(path, relative, category="asset"))
        elif top in {"scripts", "tools"}:
            contains_scripts = True
            resources.append(_resource_entry(path, relative, category="script"))
        elif top == "references":
            if path.suffix.lower() not in READABLE_SUFFIXES | {".docx", ".xlsx", ".pptx", ".pdf"}:
                raise SkillPackageError(f"Unsupported reference type: {relative}")
            resources.append(_resource_entry(path, relative, category="reference"))
        elif top == "assets":
            suffix = path.suffix.lower()
            if (
                suffix not in READABLE_SUFFIXES | IMAGE_SUFFIXES | TEMPLATE_SUFFIXES
                and not is_env_resource(path)
            ):
                raise SkillPackageError(f"Unsupported asset type: {relative}")
            resources.append(_resource_entry(path, relative, category="asset"))

    return ParsedSkillPackage(
        code=code,
        label=label,
        description=description,
        version=version,
        schema_version=schema_version,
        content_hash=content_hash,
        manifest={**manifest, "skill_frontmatter": frontmatter},
        resource_index=resources,
        contains_scripts=contains_scripts,
        package_root=package_root,
    )


def parse_skill_zip(data: bytes, *, workdir: Path | None = None) -> ParsedSkillPackage:
    if not data:
        raise SkillPackageError("Skill ZIP is empty")
    if len(data) > MAX_ZIP_BYTES:
        raise SkillPackageError(f"Skill ZIP exceeds {MAX_ZIP_BYTES} bytes")
    content_hash = hashlib.sha256(data).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillPackageError("Skill package is not a valid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if len([item for item in infos if not item.is_dir()]) > MAX_FILES:
            raise SkillPackageError(f"Skill ZIP exceeds {MAX_FILES} files")
        names = [_validate_member(info) for info in infos]
        if len({str(name) for name in names}) != len(names):
            raise SkillPackageError("Skill ZIP contains duplicate paths")
        total = sum(info.file_size for info in infos if not info.is_dir())
        if total > MAX_EXTRACTED_BYTES:
            raise SkillPackageError(f"Extracted Skill exceeds {MAX_EXTRACTED_BYTES} bytes")
        root = _archive_root(names)
        base = workdir or Path(tempfile.mkdtemp(prefix="deep-agent-skill-TMP-to-delete-"))
        base.mkdir(parents=True, exist_ok=True)
        extract_root = base / "package"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            extracted_bytes = 0
            for info, member in zip(infos, names, strict=True):
                if info.is_dir():
                    continue
                relative = member.relative_to(root) if root != PurePosixPath(".") else member
                target = extract_root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        extracted_bytes += len(chunk)
                        if extracted_bytes > MAX_EXTRACTED_BYTES:
                            raise SkillPackageError(
                                f"Extracted Skill exceeds {MAX_EXTRACTED_BYTES} bytes"
                            )
                        destination.write(chunk)
            parsed = inspect_skill_directory(
                extract_root,
                content_hash=content_hash,
                require_directory_match=False,
            )
            # Construct the public Harness capability to validate its current parser semantics.
            from pydantic_ai_harness.skills import Skills

            library = base / "harness-library"
            library.mkdir(exist_ok=True)
            skill_dir = library / parsed.code
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            shutil.copytree(extract_root, skill_dir)
            Skills(library, include=[parsed.code])
            return ParsedSkillPackage(**{**parsed.__dict__, "package_root": extract_root})
        except Exception:
            if workdir is None:
                shutil.rmtree(base, ignore_errors=True)
            raise
