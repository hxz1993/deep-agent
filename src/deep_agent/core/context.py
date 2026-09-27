from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.core.session_live import SessionLiveHub
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.capabilities.mcp.builtin_config import is_shipped_builtin
from deep_agent.capabilities.mcp.inspection import inspect_mcp_server
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.factory import SandboxInterpreterError, build_sandbox_backend
from deep_agent.sandbox.venv import ensure_sandbox_interpreter_for_startup
from deep_agent.sandbox.profile import (
    DEFAULT_SANDBOX_PROFILE,
    DefaultProfileVerifier,
    ProfileMismatchError,
    ResolvedSandboxProfile,
    load_sandbox_profile,
    resolve_sandbox_profile,
)
from loguru import logger

from deep_agent.db.factory import bootstrap_database, close_db
from deep_agent.db.stores.bundle import DataStores
from deep_agent.db.conversation import SessionStore
from deep_agent.files.storage import FileStore, PlatformFileStore, build_file_store
from deep_agent.options import OPTION, validate_runtime_options
from deep_agent.security import ConsoleAdminAuthorizer
from deep_agent.schemas.mcp import McpInspection
from deep_agent.skills.runtime import SkillRuntimeLibrary

_context: AppContext | None = None


async def _hydrate_builtin_mcp_catalogs(stores: DataStores) -> None:
    """Fill missing builtin catalog snapshots without changing existing inspections."""
    for row in await stores.mcp.list_all():
        if row.transport != "builtin" or not is_shipped_builtin(row.name):
            continue
        previous = McpInspection.from_value(row.inspection, transport="builtin")
        if previous.tools.snapshot is not None and (
            previous.resources.count == 0 or previous.resources.definitions is not None
        ):
            continue
        try:
            result = await inspect_mcp_server(row)
            if result.inspection.tools.snapshot is not None:
                await stores.mcp.update_inspection(row.name, result.inspection.to_storage_value())
        except Exception as exc:  # noqa: BLE001 - A catalog must not block startup
            logger.warning("Builtin MCP catalog initialization failed name={} error_type={}", row.name, type(exc).__name__)


@dataclass
class AppContext:
    registry: ToolPackageRegistry
    stores: DataStores
    mcp_lease_manager: McpLeaseManager
    event_persister: EventPersister
    db_engine: AsyncEngine
    db_session_factory: async_sessionmaker[AsyncSession]
    active_runs: ActiveRunRegistry
    skill_runtime: SkillRuntimeLibrary
    console_admin_authorizer: ConsoleAdminAuthorizer
    file_store: FileStore
    sandbox_backend: PythonExecutionBackend | None = None
    sandbox_profile: ResolvedSandboxProfile | None = None
    session_live: SessionLiveHub = field(default_factory=SessionLiveHub)
    wecom_profiles: object | None = None
    wecom_import_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get_wecom_profiles(self):
        # One service per API process, shared by gateway and administrator calls.
        if self.wecom_profiles is None:
            from deep_agent.services.wecom_profiles import WecomProfileService
            self.wecom_profiles = WecomProfileService(self.stores.wecom, self.stores.channel)
        return self.wecom_profiles

    @property
    def store(self) -> SessionStore:
        return self.stores.coordinator

    @classmethod
    async def bootstrap(cls) -> AppContext:
        validate_runtime_options()
        mcp_lease_manager = McpLeaseManager(mcp_acquire_retries=OPTION.agent.mcp_acquire_retries)

        try:
            bundle = await bootstrap_database()
        except Exception:
            logger.exception("Startup aborted: database migration failed")
            raise

        logger.info(
            "{} started (database={})",
            OPTION.agent.app_name,
            bundle.dialect.value,
        )
        interrupted = await bundle.stores.coordinator.interrupt_running()
        if interrupted:
            logger.warning(
                "Recovered {} unfinished Compaction Events as interrupted",
                interrupted,
            )
        file_store = await build_file_store(
            backend=OPTION.agent.file_backend,
            local_root=OPTION.agent.local_file_root,
            database_dialect=bundle.dialect,
        )

        try:
            ensure_sandbox_interpreter_for_startup()
            sandbox_backend = build_sandbox_backend()
        except SandboxInterpreterError:
            logger.exception("Startup aborted: sandbox interpreter unavailable")
            raise

        resolved_profile = None
        if sandbox_backend is not None:
            manifest = load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
            verifier = DefaultProfileVerifier()
            try:
                probe = await verifier.verify_strict(
                    manifest,
                    sandbox_backend,
                    timeout_ms=int(OPTION.agent.sandbox_timeout * 1000),
                )
            except ProfileMismatchError as exc:
                python_hint = ""
                if hasattr(sandbox_backend, "python_executable"):
                    python_hint = f" interpreter={sandbox_backend.python_executable}"
                logger.error(
                    "Startup aborted: sandbox profile mismatch profile={} missing={}{}",
                    exc.probe.profile_id,
                    exc.probe.missing_imports,
                    python_hint,
                )
                raise RuntimeError(
                    f"sandbox profile {exc.probe.profile_id} mismatch: "
                    f"missing={exc.probe.missing_imports}{python_hint}"
                ) from exc
            resolved_profile = resolve_sandbox_profile(manifest, probe)
            logger.info(
                "Sandbox profile ready profile={} packages={}",
                resolved_profile.manifest.id,
                list(resolved_profile.package_display_names),
            )

        skill_runtime = SkillRuntimeLibrary(file_store=file_store)
        try:
            await skill_runtime.reconcile(bundle.stores)
        except Exception as exc:  # noqa: BLE001 - Skill files must not block service startup
            logger.warning(
                "Skill runtime startup reconcile failed error_type={}",
                type(exc).__name__,
            )

        ctx = cls(
            registry=build_registry(
                stores=bundle.stores,
                sandbox_backend=sandbox_backend,
                sandbox_profile=resolved_profile,
            ),
            stores=bundle.stores,
            mcp_lease_manager=mcp_lease_manager,
            event_persister=EventPersister(bundle.stores.coordinator),
            active_runs=ActiveRunRegistry(),
            skill_runtime=skill_runtime,
            console_admin_authorizer=ConsoleAdminAuthorizer(),
            file_store=file_store,
            db_engine=bundle.engine,
            db_session_factory=bundle.session_factory,
            sandbox_backend=sandbox_backend,
            sandbox_profile=resolved_profile,
        )
        await ctx.event_persister.start()
        init_context(ctx)
        await _hydrate_builtin_mcp_catalogs(ctx.stores)
        return ctx

    @classmethod
    def for_testing(
        cls,
        *,
        registry: ToolPackageRegistry | None = None,
        stores: DataStores | None = None,
        mcp_lease_manager: McpLeaseManager | None = None,
        mcp_acquire_retries: int | None = None,
        event_persister: EventPersister | None = None,
        db_engine: AsyncEngine | None = None,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        sandbox_backend: PythonExecutionBackend | None = None,
        sandbox_profile: ResolvedSandboxProfile | None = None,
        active_runs: ActiveRunRegistry | None = None,
        skill_runtime: SkillRuntimeLibrary | None = None,
        console_admin_authorizer: ConsoleAdminAuthorizer | None = None,
        file_store: FileStore | None = None,
    ) -> AppContext:
        if mcp_lease_manager is None:
            retries = (
                OPTION.agent.mcp_acquire_retries
                if mcp_acquire_retries is None
                else mcp_acquire_retries
            )
            mcp_lease_manager = McpLeaseManager(mcp_acquire_retries=retries)
        if stores is None:
            raise ValueError("for_testing requires explicit stores (use tests.helpers.sqlite_stores)")
        if db_engine is None or db_session_factory is None:
            raise ValueError("for_testing requires db_engine and db_session_factory when using SQL stores")
        if event_persister is None:
            event_persister = EventPersister(stores.coordinator)
        resolved_registry = registry
        if resolved_registry is None:
            if sandbox_backend is not None:
                resolved_registry = build_registry(
                    sandbox_backend=sandbox_backend,
                    sandbox_profile=sandbox_profile,
                )
            else:
                resolved_registry = ToolPackageRegistry()

        return cls(
            registry=resolved_registry,
            stores=stores,
            mcp_lease_manager=mcp_lease_manager,
            event_persister=event_persister,
            active_runs=active_runs or ActiveRunRegistry(),
            skill_runtime=skill_runtime
            or SkillRuntimeLibrary(
                Path(tempfile.mkdtemp(prefix="deep-agent-runtime-skills-TMP_to_delete-")),
                file_store=file_store or PlatformFileStore(),
            ),
            console_admin_authorizer=console_admin_authorizer
            or ConsoleAdminAuthorizer.for_testing(),
            file_store=file_store or PlatformFileStore(),
            db_engine=db_engine,
            db_session_factory=db_session_factory,
            sandbox_backend=sandbox_backend,
            sandbox_profile=sandbox_profile,
        )

    async def aclose(self) -> None:
        if self.wecom_profiles is not None:
            await self.wecom_profiles.close()
        self.session_live.close()
        self.active_runs.cancel_all()
        await self.mcp_lease_manager.close_all()
        if self.sandbox_backend is not None:
            await self.sandbox_backend.aclose()
        await self.event_persister.aclose()
        await close_db(self.db_engine)


def init_context(ctx: AppContext) -> None:
    global _context
    _context = ctx


def reset_context() -> None:
    global _context
    _context = None


def get_context() -> AppContext:
    if _context is None:
        raise RuntimeError("Application context is not initialized")
    return _context


async def get_app_context() -> AppContext:
    return get_context()


AppContextDep = Annotated[AppContext, Depends(get_app_context)]
