from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
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
from deep_agent.skills.runtime import SkillRuntimeLibrary

_context: AppContext | None = None


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
                "Recovered {} unfinished Run/Compaction Events as interrupted",
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
