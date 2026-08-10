from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from loguru import logger
from pydantic_ai_harness.skills import Skills

from deep_agent.db.models import SkillPackage
from deep_agent.db.stores.bundle import DataStores
from deep_agent.skills.catalog import BUILTIN_SKILLS_ROOT, SkillDescriptor, get_builtin_skill
from deep_agent.skills.package import ParsedSkillPackage, parse_skill_zip
from deepfos_platform.client.platform import PlatformAPI


SKILL_CACHE_ROOT = Path(tempfile.gettempdir()) / "deep-agent-skill-cache"
MAX_CACHE_ENTRIES = 32


class SkillUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSkill:
    code: str
    source: str
    enabled: bool
    package_root: Path
    library_root: Path
    resource_index: list[dict]


@dataclass(frozen=True)
class ResolvedSkills:
    items: tuple[ResolvedSkill, ...]
    capability: Skills | None

    @property
    def resources(self) -> dict[str, ResolvedSkill]:
        return {
            item.code: item
            for item in self.items
            if any(entry.get("category") != "script" for entry in item.resource_index)
        }


_cache_locks: dict[str, asyncio.Lock] = {}


def _cache_lock(content_hash: str) -> asyncio.Lock:
    return _cache_locks.setdefault(content_hash, asyncio.Lock())


def _row_resource_index(row: SkillPackage) -> list[dict]:
    return [dict(item) for item in (row.resource_index or [])]


async def _download_package(row: SkillPackage, request_headers: Mapping[str, str]) -> bytes:
    api = PlatformAPI(header=dict(request_headers))
    payload = await api.file.download(row.platform_file_id)
    if not isinstance(payload, (bytes, bytearray)):
        raise SkillUnavailableError(f"Platform file for Skill {row.code!r} did not return bytes")
    return bytes(payload)


def _prune_cache() -> None:
    if not SKILL_CACHE_ROOT.is_dir():
        return
    entries = sorted(
        (item for item in SKILL_CACHE_ROOT.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in entries[MAX_CACHE_ENTRIES:]:
        shutil.rmtree(stale, ignore_errors=True)


async def materialize_uploaded_skill(
    row: SkillPackage,
    request_headers: Mapping[str, str],
) -> ResolvedSkill:
    destination = SKILL_CACHE_ROOT / row.content_hash
    marker = destination / "cache.json"
    async with _cache_lock(row.content_hash):
        if marker.is_file():
            try:
                state = json.loads(marker.read_text("utf-8"))
            except (OSError, ValueError):
                state = {}
            if state.get("content_hash") == row.content_hash and (destination / "package").is_dir():
                marker.touch()
                return ResolvedSkill(
                    code=row.code,
                    source="upload",
                    enabled=row.enabled,
                    package_root=destination / "package",
                    library_root=destination / "harness-library",
                    resource_index=_row_resource_index(row),
                )
            shutil.rmtree(destination, ignore_errors=True)

        payload = await _download_package(row, request_headers)
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != row.content_hash:
            raise SkillUnavailableError(
                f"Skill {row.code!r} package hash mismatch: expected {row.content_hash}, got {actual_hash}"
            )
        SKILL_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        staging = SKILL_CACHE_ROOT / f"{row.content_hash}-{uuid.uuid4().hex}-TMP-to-delete"
        try:
            parsed: ParsedSkillPackage = parse_skill_zip(payload, workdir=staging)
            if parsed.code != row.code:
                raise SkillUnavailableError(
                    f"Skill package code changed from {row.code!r} to {parsed.code!r}"
                )
            marker_data = {
                "content_hash": row.content_hash,
                "code": row.code,
                "materialized_at": int(time.time()),
            }
            (staging / "cache.json").write_text(
                json.dumps(marker_data, ensure_ascii=False), encoding="utf-8"
            )
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        await asyncio.to_thread(_prune_cache)
        logger.info("Materialized uploaded Skill code={} hash={}", row.code, row.content_hash[:12])
        return ResolvedSkill(
            code=row.code,
            source="upload",
            enabled=row.enabled,
            package_root=destination / "package",
            library_root=destination / "harness-library",
            resource_index=_row_resource_index(row),
        )


def _resolved_builtin(item: SkillDescriptor) -> ResolvedSkill:
    return ResolvedSkill(
        code=item.code,
        source="builtin",
        enabled=True,
        package_root=item.package_root,
        library_root=BUILTIN_SKILLS_ROOT,
        resource_index=[dict(entry) for entry in item.resource_index],
    )


async def validate_skill_codes(stores: DataStores, codes: list[str]) -> list[str]:
    invalid: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            invalid.append(code)
            continue
        seen.add(code)
        builtin = get_builtin_skill(code)
        if builtin is not None:
            continue
        row = await stores.skill_package.get_by_code(code)
        if row is None or not row.enabled:
            invalid.append(code)
    return invalid


async def resolve_skills_for_run(
    stores: DataStores,
    codes: list[str],
    *,
    request_headers: Mapping[str, str],
) -> ResolvedSkills:
    if not codes:
        return ResolvedSkills(items=(), capability=None)
    invalid = await validate_skill_codes(stores, codes)
    if invalid:
        raise SkillUnavailableError(
            "Unknown, disabled or duplicate Skills: " + ", ".join(invalid)
        )

    items: list[ResolvedSkill] = []
    for code in codes:
        builtin = get_builtin_skill(code)
        if builtin is not None:
            items.append(_resolved_builtin(builtin))
            continue
        row = await stores.skill_package.get_by_code(code)
        if row is None or not row.enabled:
            raise SkillUnavailableError(f"Skill {code!r} is unavailable")
        items.append(await materialize_uploaded_skill(row, request_headers))

    directories: list[Path] = []
    for item in items:
        if item.library_root not in directories:
            directories.append(item.library_root)
    capability = Skills(directories, include=codes)
    return ResolvedSkills(items=tuple(items), capability=capability)
