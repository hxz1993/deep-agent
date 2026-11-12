"""Resolve a Run's application from its owning session and caller's platform access."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException
from loguru import logger

from deep_agent.diagnostics import emit
from deep_agent.integrations.app_scope import INTEGRATION, app_skill_scope
from deep_agent.security.console_admin import ConsoleAdminAuthenticationError, forwarded_platform_auth_headers
from deep_agent.services.scope_names import PlatformScopeDirectoryFetcher, ScopeDirectoryUnavailableError


@dataclass(frozen=True)
class RunAppContext:
    app_id: str
    space_id: str
    prompt_append: str = ""
    skill_scope: str | None = None
    skill_codes: tuple[str, ...] = ()


async def resolve_run_app(
    request, ctx, *, agent_id: str, user: str, session_id: str | None, headers, channel_account=None,
) -> RunAppContext | None:
    requested = (headers.get("app"), headers.get("space"))
    saved = await ctx.stores.chat_session.find_initial_scope(agent_id, session_id) if session_id else None
    app_id, space_id = saved if saved is not None else requested
    if not app_id or not space_id:
        return None

    scope = None
    if ctx.stores.integration_app is not None and ctx.stores.integration_settings is not None:
        binding = await ctx.stores.integration_settings.get_by_code(INTEGRATION)
        if binding is not None and binding.settings.get("agent_id") == agent_id:
            scope = await ctx.stores.integration_app.get(app_id, space_id)

    started = monotonic()

    def record(status: str, *, level: str = "INFO") -> None:
        if scope is not None:
            emit(
                "integration_app_context", level=level, session_id=session_id, agent_id=agent_id,
                app_id=app_id, space_id=space_id, status=status,
                elapsed_ms=round((monotonic() - started) * 1000),
            )

    try:
        auth = forwarded_platform_auth_headers(request, user)
        if channel_account is not None:
            if requested != (app_id, space_id):
                record("scope_mismatch")
                return None
            key = headers.get("x-deepfos-key")
            if key:
                auth = {"x-deepfos-key": key, "user": user, "app": app_id, "space": space_id}
        if not any(auth.get(name) for name in ("authorization", "cookie", "token", "x-user-token", "x-deepfos-key")):
            record("missing_credentials")
            return None
        if auth.get("x-deepfos-key") and (auth.get("app"), auth.get("space")) != (app_id, space_id):
            record("scope_mismatch")
            return None
        # A fresh permission lookup is needed for applying private instructions.
        # The display-name cache is not an authorization source.
        async with asyncio.timeout(2):
            directory = await PlatformScopeDirectoryFetcher()(auth, tenant_code=scope.tenant_id if scope else None)
    except TimeoutError:
        record("directory_timeout", level="WARNING")
        return None
    except (ConsoleAdminAuthenticationError, ScopeDirectoryUnavailableError, OSError):
        record("directory_unavailable", level="WARNING")
        return None
    except Exception as exc:
        # Optional platform information can be unavailable without invalidating
        # an otherwise authorized conversation. No credential or payload is logged.
        logger.warning("App context lookup failed error_type={}", type(exc).__name__)
        record("directory_unavailable", level="WARNING")
        return None

    relationship = directory.app_scopes.get(app_id)
    if relationship is None or relationship[0] != space_id:
        record("app_access_unconfirmed")
        return None
    if scope is None or relationship[1] != scope.tenant_id:
        record("tenant_mismatch")
        return RunAppContext(app_id, space_id)
    # Keep private instructions and downstream routing in the same application.
    if requested[0] is not None and requested != (app_id, space_id):
        record("scope_mismatch")
        raise HTTPException(409, detail="调试应用与会话所属应用不同，请新建对话后重试。")
    record("prompt_loaded" if scope.settings.get("prompt_append") else "config_loaded")
    return RunAppContext(
        app_id, space_id,
        prompt_append=scope.settings.get("prompt_append", ""),
        skill_scope=app_skill_scope(app_id),
        skill_codes=tuple(scope.settings["skills"]),
    )


async def ensure_scoped_run_session(ctx, app: RunAppContext | None, agent_id: str, session_id: str) -> None:
    """Check the scope again after Run admission in case a new session raced assembly."""
    if app is None or app.skill_scope is None:
        return
    saved = await ctx.stores.chat_session.find_initial_scope(agent_id, session_id)
    if saved is not None and saved != (app.app_id, app.space_id):
        raise HTTPException(409, detail="会话所属应用已变更，请新建对话后重试。")
