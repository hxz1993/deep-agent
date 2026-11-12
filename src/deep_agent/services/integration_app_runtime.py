"""Resolve application configuration for the Run's session scope."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException, Request

from deep_agent.diagnostics import emit
from deep_agent.integrations.app_scope import app_skill_scope


@dataclass(frozen=True)
class RunAppContext:
    app_id: str
    space_id: str
    prompt_append: str = ""
    skill_scope: str | None = None
    skill_codes: tuple[str, ...] = ()


async def resolve_run_app(
    request: Request, ctx, *, agent_id: str, session_id: str | None, headers, channel_account=None,
) -> RunAppContext | None:
    requested = (headers.get("app"), headers.get("space"))
    saved = await ctx.stores.chat_session.find_initial_scope(agent_id, session_id) if session_id else None
    app_id, space_id = saved if saved is not None else requested
    if not app_id or not space_id:
        return None

    # A channel or an app-scoped key fixes the scope of downstream tool calls.
    if channel_account is not None:
        if requested != (app_id, space_id):
            return None
    elif headers.get("x-deepfos-key"):
        key_scope = (request.headers.get("app", "").strip(), request.headers.get("space", "").strip())
        if key_scope != (app_id, space_id) or requested != key_scope:
            return None

    started = monotonic()
    store = ctx.stores.integration_app
    scope = await store.get_for_agent(app_id, space_id, agent_id) if store is not None else None
    if scope is None:
        return RunAppContext(app_id, space_id)
    # Keep application instructions and downstream routing in the same scope.
    if requested[0] is not None and requested != (app_id, space_id):
        raise HTTPException(409, detail="调试应用与会话所属应用不同，请新建对话后重试。")
    prompt = scope.settings.get("prompt_append", "")
    emit(
        "integration_app_context", session_id=session_id, agent_id=agent_id, app_id=app_id, space_id=space_id,
        status="prompt_loaded" if prompt else "config_loaded",
        elapsed_ms=round((monotonic() - started) * 1000),
    )
    return RunAppContext(
        app_id, space_id,
        prompt_append=prompt,
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
