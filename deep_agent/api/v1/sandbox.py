from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from deep_agent.core.context import AppContextDep
from deep_agent.options import OPTION
from deep_agent.sandbox.profile import DEFAULT_SANDBOX_PROFILE, ResolvedSandboxProfile
from deep_agent.sandbox.types import (
    ExecuteRequest,
    ResourceLimitsSummary,
    SecurityLevel,
    StateScope,
)
from loguru import logger

router = APIRouter()

_SMOKE_CODE = "1 + 1"


class SandboxHealthDisabledResponse(BaseModel):
    enabled: bool


class SandboxHealthEnabledResponse(BaseModel):
    enabled: bool
    backend: str
    expected_profile: str
    profile_ok: bool
    package_names: list[str]
    missing_packages: list[str]
    state_scope: StateScope
    security_level: SecurityLevel
    production_ready: bool
    smoke_ok: bool
    resource_limits: ResourceLimitsSummary | None = None


async def build_sandbox_health_response(
    *,
    sandbox_backend,
    sandbox_profile: ResolvedSandboxProfile | None = None,
) -> SandboxHealthDisabledResponse | SandboxHealthEnabledResponse:
    if sandbox_backend is None:
        return SandboxHealthDisabledResponse(enabled=False)

    health = await sandbox_backend.health()
    smoke = await sandbox_backend.execute(
        ExecuteRequest(
            request_id=uuid.uuid4().hex,
            code=_SMOKE_CODE,
            inputs={},
            timeout_ms=int(OPTION.agent.sandbox_timeout * 1000),
        )
    )
    smoke_ok = smoke.status == "ok" and smoke.result == 2 and smoke.error is None

    profile_id = (
        sandbox_profile.manifest.id
        if sandbox_profile is not None
        else DEFAULT_SANDBOX_PROFILE
    )
    profile_ok = False
    package_names: list[str] = []
    missing_packages: list[str] = []
    if sandbox_profile is not None:
        profile_ok = sandbox_profile.profile_ok
        package_names = list(sandbox_profile.package_display_names)
        missing_packages = list(sandbox_profile.probe.missing_imports)

    return SandboxHealthEnabledResponse(
        enabled=health.enabled,
        backend=health.backend,
        expected_profile=profile_id,
        profile_ok=profile_ok,
        package_names=package_names,
        missing_packages=missing_packages,
        state_scope=health.state_scope,
        security_level=health.security_level,
        production_ready=health.production_ready,
        smoke_ok=smoke_ok,
        resource_limits=health.resource_limits,
    )


@router.get("/health/sandbox")
async def sandbox_health(
    ctx: AppContextDep,
) -> SandboxHealthDisabledResponse | SandboxHealthEnabledResponse:
    """Report sandbox availability and run a fixed smoke execution."""
    response = await build_sandbox_health_response(
        sandbox_backend=ctx.sandbox_backend,
        sandbox_profile=ctx.sandbox_profile,
    )
    if isinstance(response, SandboxHealthEnabledResponse):
        logger.info(
            "Sandbox health backend={} probe_ok={} profile_ok={} production_ready={}",
            response.backend,
            response.smoke_ok,
            response.profile_ok,
            response.production_ready,
        )
    return response
