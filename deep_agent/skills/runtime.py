from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from loguru import logger
from pydantic_ai_harness.skills import Skills

from deep_agent.db.models import SkillPackage
from deep_agent.db.stores.bundle import DataStores
from deep_agent.skills.catalog import BUILTIN_SKILLS_ROOT, SkillDescriptor, get_builtin_skill
from deep_agent.skills.package import ParsedSkillPackage, SkillPackageError, parse_skill_zip
from deepfos_platform.client.platform import PlatformAPI
from deepfos_platform.lib.constants import INTERNAL_TOKEN_HEADER
from deepfos_platform.options import OPTION as PLATFORM_OPTION


SKILL_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "deep-agent-runtime-skills"
SKILL_RUNTIME_MARKER = ".deepagent-runtime.json"
SKILL_STARTUP_SYNC_TIMEOUT_SECONDS = 30
SKILL_DOWNLOAD_TIMEOUT_SECONDS = 15
SKILL_SYNC_CONCURRENCY = 4

RUNTIME_READY = "ready"
RUNTIME_MISSING = "missing"
RUNTIME_SYNCING = "syncing"
RUNTIME_ERROR = "error"
RUNTIME_DISABLED = "disabled"


class SkillUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class SkillRuntimeState:
    code: str
    status: str
    error_code: str | None = None
    synced_at: datetime | None = None
    content_hash: str | None = None
    platform_file_id: str | None = None


@dataclass(frozen=True)
class SkillRuntimeIssue:
    code: str
    reason: str


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
    configured: tuple[str, ...]
    items: tuple[ResolvedSkill, ...]
    unavailable: tuple[SkillRuntimeIssue, ...]
    capability: Skills | None

    @property
    def resources(self) -> dict[str, ResolvedSkill]:
        return {
            item.code: item
            for item in self.items
            if any(entry.get("category") != "script" for entry in item.resource_index)
        }

    @property
    def available_codes(self) -> list[str]:
        return [item.code for item in self.items]

    def as_context(self) -> dict[str, object]:
        return {
            "configured": list(self.configured),
            "available": self.available_codes,
            "loaded": [],
            "unavailable": [
                {"code": item.code, "reason": item.reason}
                for item in self.unavailable
            ],
        }


def _row_resource_index(row: SkillPackage) -> list[dict]:
    return [dict(item) for item in (row.resource_index or [])]


def skill_platform_file_headers(
    request_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build identity headers for a system-level Skill file operation.

    Uploaded Skill packages are shared configuration, not Space-owned business
    files. Preserve the caller/service identity but deliberately strip the
    App/Space/Language business context so upload, restart recovery and
    Run-time fallback all address the same system-level file.
    """

    headers = dict(request_headers or {})
    for name in tuple(headers):
        if name.lower() in {
            "app",
            "space",
            "language",
            INTERNAL_TOKEN_HEADER.lower(),
        }:
            headers.pop(name, None)
    token = PLATFORM_OPTION.general.internal_token
    if token:
        headers[INTERNAL_TOKEN_HEADER] = token
    return headers


def _raise_platform_download_error(payload: bytes) -> None:
    stripped = payload.lstrip()
    if not stripped.startswith(b"{"):
        return
    try:
        body = json.loads(stripped.decode("utf-8"))
    except (UnicodeError, ValueError):
        return
    if isinstance(body, dict) and body.get("status") is False:
        message = str(body.get("message") or "platform file download failed")
        if "路径为空" in message or "not found" in message.lower():
            raise KeyError(message)
        raise SkillUnavailableError(message)


def _runtime_error_code(exc: BaseException) -> str:
    if isinstance(exc, SkillPackageError):
        return "invalid_package"
    if isinstance(exc, KeyError):
        return "platform_file_missing"
    text = str(exc).lower()
    if "hash mismatch" in text:
        return "hash_mismatch"
    if "404" in text or "not found" in text or "does not exist" in text:
        return "platform_file_missing"
    return "download_failed"


class SkillRuntimeLibrary:
    """One current, rebuildable Skill directory per uploaded Skill code."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or SKILL_RUNTIME_ROOT).resolve()
        self._states: dict[str, SkillRuntimeState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def state(self, code: str, *, enabled: bool = True) -> SkillRuntimeState:
        if not enabled:
            return SkillRuntimeState(code=code, status=RUNTIME_DISABLED)
        return self._states.get(code, SkillRuntimeState(code=code, status=RUNTIME_MISSING))

    def state_for(self, row: SkillPackage) -> SkillRuntimeState:
        current = self.state(row.code, enabled=row.enabled)
        if (
            current.status == RUNTIME_READY
            and (
                current.content_hash != row.content_hash
                or current.platform_file_id != row.platform_file_id
            )
        ):
            return SkillRuntimeState(code=row.code, status=RUNTIME_MISSING)
        return current

    def _lock(self, code: str) -> asyncio.Lock:
        return self._locks.setdefault(code, asyncio.Lock())

    def _destination(self, code: str) -> Path:
        return self.root / code

    def _marker(self, code: str) -> Path:
        return self._destination(code) / SKILL_RUNTIME_MARKER

    def _ready_from_disk(self, row: SkillPackage) -> bool:
        destination = self._destination(row.code)
        marker = destination / SKILL_RUNTIME_MARKER
        if not marker.is_file() or not (destination / "SKILL.md").is_file():
            return False
        try:
            state = json.loads(marker.read_text("utf-8"))
        except (OSError, ValueError):
            return False
        return (
            state.get("code") == row.code
            and state.get("content_hash") == row.content_hash
            and state.get("platform_file_id") == row.platform_file_id
        )

    def _synced_at_from_disk(self, row: SkillPackage) -> datetime | None:
        try:
            value = json.loads(self._marker(row.code).read_text("utf-8")).get(
                "materialized_at"
            )
            return datetime.fromisoformat(value) if value else None
        except (OSError, TypeError, ValueError):
            return None

    def _resolved(self, row: SkillPackage) -> ResolvedSkill:
        return ResolvedSkill(
            code=row.code,
            source="upload",
            enabled=row.enabled,
            package_root=self._destination(row.code),
            library_root=self.root,
            resource_index=_row_resource_index(row),
        )

    async def _download_package(
        self,
        row: SkillPackage,
        request_headers: Mapping[str, str],
    ) -> bytes:
        api = PlatformAPI(header=dict(request_headers))
        async with asyncio.timeout(SKILL_DOWNLOAD_TIMEOUT_SECONDS):
            payload = await api.file.download(row.platform_file_id)
        if not isinstance(payload, (bytes, bytearray)):
            raise SkillUnavailableError(
                f"Platform file for Skill {row.code!r} did not return bytes"
            )
        raw = bytes(payload)
        _raise_platform_download_error(raw)
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != row.content_hash:
            raise SkillUnavailableError(
                f"Skill {row.code!r} package hash mismatch: "
                f"expected {row.content_hash}, got {actual_hash}"
            )
        return raw

    async def download_package(
        self,
        row: SkillPackage,
        *,
        request_headers: Mapping[str, str] | None = None,
    ) -> bytes:
        return await self._download_package(
            row,
            skill_platform_file_headers(request_headers),
        )

    def _install_payload(self, row: SkillPackage, payload: bytes) -> None:
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != row.content_hash:
            raise SkillUnavailableError(
                f"Skill {row.code!r} package hash mismatch: "
                f"expected {row.content_hash}, got {actual_hash}"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f".{row.code}-{uuid.uuid4().hex}-TMP_to_delete"
        old = self.root / f".{row.code}-{uuid.uuid4().hex}-old-TMP_to_delete"
        parsed: ParsedSkillPackage | None = None
        destination = self._destination(row.code)
        try:
            parsed = parse_skill_zip(payload, workdir=staging)
            if parsed.code != row.code:
                raise SkillUnavailableError(
                    f"Skill package code changed from {row.code!r} to {parsed.code!r}"
                )
            package = parsed.package_root
            marker_data = {
                "code": row.code,
                "content_hash": row.content_hash,
                "platform_file_id": row.platform_file_id,
                "materialized_at": datetime.now(timezone.utc).isoformat(),
            }
            (package / SKILL_RUNTIME_MARKER).write_text(
                json.dumps(marker_data, ensure_ascii=False),
                encoding="utf-8",
            )
            if destination.exists():
                destination.replace(old)
            package.replace(destination)
            shutil.rmtree(old, ignore_errors=True)
        except Exception:
            if old.exists() and not destination.exists():
                old.replace(destination)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)

    async def materialize(
        self,
        row: SkillPackage,
        *,
        request_headers: Mapping[str, str] | None = None,
        payload: bytes | None = None,
        force: bool = False,
    ) -> SkillRuntimeState:
        if not row.enabled:
            await self.remove(row.code, status=RUNTIME_DISABLED)
            return self.state(row.code, enabled=False)

        async with self._lock(row.code):
            if not force and self._ready_from_disk(row):
                previous = self._states.get(row.code)
                state = SkillRuntimeState(
                    code=row.code,
                    status=RUNTIME_READY,
                    synced_at=(
                        previous.synced_at
                        if previous and previous.synced_at
                        else self._synced_at_from_disk(row)
                    ),
                    content_hash=row.content_hash,
                    platform_file_id=row.platform_file_id,
                )
                self._states[row.code] = state
                return state

            preserved_ready = self._ready_from_disk(row)
            self._states[row.code] = SkillRuntimeState(
                code=row.code,
                status=RUNTIME_SYNCING,
            )
            try:
                raw = payload
                if raw is None:
                    raw = await self.download_package(
                        row,
                        request_headers=request_headers,
                    )
                await asyncio.to_thread(self._install_payload, row, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - derived runtime state must fail open
                error_code = _runtime_error_code(exc)
                if preserved_ready and self._ready_from_disk(row):
                    self._states[row.code] = SkillRuntimeState(
                        code=row.code,
                        status=RUNTIME_READY,
                        synced_at=self._synced_at_from_disk(row),
                        content_hash=row.content_hash,
                        platform_file_id=row.platform_file_id,
                    )
                else:
                    shutil.rmtree(self._destination(row.code), ignore_errors=True)
                    self._states[row.code] = SkillRuntimeState(
                        code=row.code,
                        status=RUNTIME_ERROR,
                        error_code=error_code,
                    )
                logger.warning(
                    "Skill runtime sync failed code={} error_code={} error_type={}",
                    row.code,
                    error_code,
                    type(exc).__name__,
                )
                return SkillRuntimeState(
                    code=row.code,
                    status=RUNTIME_ERROR,
                    error_code=error_code,
                )

            state = SkillRuntimeState(
                code=row.code,
                status=RUNTIME_READY,
                synced_at=datetime.now(timezone.utc),
                content_hash=row.content_hash,
                platform_file_id=row.platform_file_id,
            )
            self._states[row.code] = state
            logger.info("Skill runtime ready code={}", row.code)
            return state

    async def resolve(
        self,
        row: SkillPackage,
        *,
        request_headers: Mapping[str, str],
    ) -> ResolvedSkill | None:
        if not row.enabled:
            self._states[row.code] = SkillRuntimeState(
                code=row.code,
                status=RUNTIME_DISABLED,
            )
            return None
        current = self.state_for(row)
        if current.status == RUNTIME_READY and (
            self._destination(row.code) / "SKILL.md"
        ).is_file():
            return self._resolved(row)
        if current.status == RUNTIME_ERROR:
            return None
        if current.status == RUNTIME_MISSING and self._ready_from_disk(row):
            self._states[row.code] = SkillRuntimeState(
                code=row.code,
                status=RUNTIME_READY,
                synced_at=self._synced_at_from_disk(row),
                content_hash=row.content_hash,
                platform_file_id=row.platform_file_id,
            )
            return self._resolved(row)
        state = await self.materialize(row, request_headers=request_headers)
        return self._resolved(row) if state.status == RUNTIME_READY else None

    async def remove(self, code: str, *, status: str = RUNTIME_MISSING) -> None:
        async with self._lock(code):
            await asyncio.to_thread(
                shutil.rmtree,
                self._destination(code),
                True,
            )
            self._states[code] = SkillRuntimeState(code=code, status=status)

    async def reconcile(self, stores: DataStores) -> None:
        rows = await stores.skill_package.list_all()
        by_code = {row.code: row for row in rows}
        enabled = {row.code for row in rows if row.enabled}
        self.root.mkdir(parents=True, exist_ok=True)

        for item in self.root.iterdir():
            if item.name.startswith("."):
                if "TMP_to_delete" in item.name:
                    shutil.rmtree(item, ignore_errors=True)
                continue
            if item.is_dir() and item.name not in enabled:
                shutil.rmtree(item, ignore_errors=True)

        for row in rows:
            if not row.enabled:
                self._states[row.code] = SkillRuntimeState(
                    code=row.code,
                    status=RUNTIME_DISABLED,
                )
            elif self._ready_from_disk(row):
                self._states[row.code] = SkillRuntimeState(
                    code=row.code,
                    status=RUNTIME_READY,
                    synced_at=self._synced_at_from_disk(row),
                    content_hash=row.content_hash,
                    platform_file_id=row.platform_file_id,
                )
            else:
                self._states[row.code] = SkillRuntimeState(
                    code=row.code,
                    status=RUNTIME_MISSING,
                )

        semaphore = asyncio.Semaphore(SKILL_SYNC_CONCURRENCY)

        async def sync_one(row: SkillPackage) -> None:
            async with semaphore:
                await self.materialize(row)

        pending = [
            sync_one(by_code[code])
            for code in sorted(enabled)
            if not self._ready_from_disk(by_code[code])
        ]
        if not pending:
            return
        try:
            async with asyncio.timeout(SKILL_STARTUP_SYNC_TIMEOUT_SECONDS):
                await asyncio.gather(*pending)
        except TimeoutError:
            logger.warning("Skill startup sync timed out pending_count={}", len(pending))
            for code in enabled:
                if self.state(code).status == RUNTIME_SYNCING:
                    self._states[code] = SkillRuntimeState(
                        code=code,
                        status=RUNTIME_ERROR,
                        error_code="download_failed",
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
    runtime: SkillRuntimeLibrary,
    request_headers: Mapping[str, str],
) -> ResolvedSkills:
    items: list[ResolvedSkill] = []
    unavailable: list[SkillRuntimeIssue] = []
    seen: set[str] = set()

    for code in codes:
        if code in seen:
            unavailable.append(SkillRuntimeIssue(code=code, reason="duplicate"))
            continue
        seen.add(code)
        builtin = get_builtin_skill(code)
        if builtin is not None:
            items.append(_resolved_builtin(builtin))
            continue
        row = await stores.skill_package.get_by_code(code)
        if row is None:
            unavailable.append(SkillRuntimeIssue(code=code, reason="unknown"))
            continue
        if not row.enabled:
            unavailable.append(SkillRuntimeIssue(code=code, reason="disabled"))
            continue
        resolved = await runtime.resolve(row, request_headers=request_headers)
        if resolved is None:
            state = runtime.state(code)
            unavailable.append(
                SkillRuntimeIssue(
                    code=code,
                    reason=state.error_code or state.status,
                )
            )
            continue
        items.append(resolved)

    if not items:
        return ResolvedSkills(
            configured=tuple(codes),
            items=(),
            unavailable=tuple(unavailable),
            capability=None,
        )

    directories: list[Path] = []
    for item in items:
        if item.library_root not in directories:
            directories.append(item.library_root)
    available_codes = [item.code for item in items]
    capability = Skills(directories, include=available_codes)
    return ResolvedSkills(
        configured=tuple(codes),
        items=tuple(items),
        unavailable=tuple(unavailable),
        capability=capability,
    )
