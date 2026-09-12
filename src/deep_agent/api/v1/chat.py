from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from deep_agent.agent.deferred import build_deferred_tool_results
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.context.compaction import ContextWindowExceededError
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.agent.run_identity import conversation_id_for, new_run_id
from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.api.v1.channel_auth import ChannelAccountDep, ChannelRequestUserDep
from deep_agent.api.v1.stream_diagnostics import DiagnosticEventSourceResponse, transport_cancel_reason
from deep_agent.core.context import AppContextDep
from deep_agent.core.exceptions import AgentModelNotConfiguredError, AgentNotFoundError, LlmModelNotFoundError
from deep_agent.db.ids import normalize_agent_id
from deep_agent.options import OPTION
from deep_agent.schemas.chat import ChatRequest, ChatResponse, StreamChatRequest


class _ChatBodyValidationRoute(APIRoute):
    """Normalize malformed/non-UTF-8 chat JSON to the API's 422 contract."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except StarletteHTTPException as exc:
                if (
                    exc.status_code == status.HTTP_400_BAD_REQUEST
                    and exc.detail == "There was an error parsing the body"
                ):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "code": "invalid_json_encoding",
                            "message": "请求体必须是有效的 UTF-8 JSON。",
                        },
                    ) from exc
                raise

        return handler


router = APIRouter(route_class=_ChatBodyValidationRoute)


def _agent_deps(
    ctx: AppContextDep,
    *,
    session_id: str,
    user: str | None,
    agent_id: str | None = None,
    request_headers: dict[str, str] | None = None,
) -> AgentDeps:
    conversation_id = conversation_id_for(agent_id, session_id)
    return AgentDeps(
        session_id=session_id,
        user=user,
        agent_id=agent_id,
        conversation_id=conversation_id,
        run_id=new_run_id(),
        request_headers=dict(request_headers or {}),
        file_store=ctx.file_store,
        app_id=(request_headers or {}).get("app"),
        space_id=(request_headers or {}).get("space"),
    )


async def _ensure_session_access(
    ctx: AppContextDep,
    session_id: str,
    *,
    user: str,
    agent_id: str | None,
) -> str:
    resolved = await ctx.store.resolve_ref(session_id, user_id=user)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    stored_agent_id, _ = resolved
    if agent_id is not None and stored_agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return stored_agent_id


async def _resolve_chat_agent_id(
    ctx: AppContextDep,
    body: ChatRequest | StreamChatRequest,
    *,
    session_id: str,
    user: str,
) -> str:
    requested: str | None = None
    if body.agent_id is not None:
        try:
            requested = normalize_agent_id(body.agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    if body.session_id is not None:
        existing = await ctx.store.resolve_ref(session_id)
        if existing is not None:
            return await _ensure_session_access(
                ctx,
                session_id,
                user=user,
                agent_id=requested,
            )
        if requested is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="agent_id is required when creating a session.",
            )
        return requested
    if requested is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="agent_id is required when creating a session.",
        )
    return requested


def _validate_chat_body(body: ChatRequest | StreamChatRequest) -> None:
    has_message = bool(body.message and body.message.strip())
    has_attachments = bool(body.attachments)
    has_context_refs = bool(body.context_refs)
    deferred = getattr(body, "deferred_tool_results", None)
    has_deferred = bool(deferred)
    if not has_message and not has_attachments and not has_context_refs and not has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either message, attachments, context_refs, or deferred_tool_results is required.",
        )
    if body.attachments and len(body.attachments) > OPTION.agent.upload_max_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"At most {OPTION.agent.upload_max_count} attachments per message.",
        )
    if body.branch_from is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_from is not implemented for the Session Event Store.",
        )
    if has_context_refs and has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="context_refs and deferred_tool_results are mutually exclusive.",
        )


def _deferred_results(body: StreamChatRequest):
    if not body.deferred_tool_results:
        return None
    return build_deferred_tool_results(body.deferred_tool_results)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    ctx: AppContextDep,
    body: ChatRequest,
    user: ChannelRequestUserDep,
    channel_account: ChannelAccountDep,
) -> ChatResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    request_headers = extract_forward_headers(request)
    request_headers["user"] = user
    agent_id = await _resolve_chat_agent_id(
        ctx, body, session_id=session_id, user=user
    )
    try:
        runner = await build_runner_for_agent(
            ctx,
            agent_id,
            model_id=body.model_id,
            capabilities=body.capabilities,
            request_headers=request_headers,
            channel_type=channel_account.channel_type if channel_account is not None else None,
        )
    except AgentModelNotConfiguredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (AgentNotFoundError, LlmModelNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    deps = _agent_deps(
        ctx,
        session_id=session_id,
        user=user,
        agent_id=agent_id,
        request_headers=request_headers,
    )
    try:
        result = await runner.run(
            session_id,
            body.message or "",
            deps,
            attachments=body.attachments,
            context_refs=body.context_refs,
            branch_from=body.branch_from,
        )
    except ContextWindowExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return ChatResponse(
        session_id=session_id,
        conversation_id=deps.conversation_id,
        run_id=deps.run_id,
        output=result["output"],
        usage=result["usage"],
        status=result.get("status", "completed"),
        deferred=result.get("deferred"),
        context=result.get("context"),
        run_metrics=result.get("run_metrics"),
    )


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    ctx: AppContextDep,
    body: StreamChatRequest,
    user: ChannelRequestUserDep,
    channel_account: ChannelAccountDep,
) -> DiagnosticEventSourceResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    deferred_tool_results = _deferred_results(body)
    message = body.message or ""
    request_headers = extract_forward_headers(request)
    request_headers["user"] = user

    agent_id = await _resolve_chat_agent_id(
        ctx, body, session_id=session_id, user=user
    )
    try:
        runner = await build_runner_for_agent(
            ctx,
            agent_id,
            model_id=body.model_id,
            capabilities=body.capabilities,
            request_headers=request_headers,
            channel_type=channel_account.channel_type if channel_account is not None else None,
        )
    except AgentModelNotConfiguredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LlmModelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    deps = _agent_deps(
        ctx,
        session_id=session_id,
        user=user,
        agent_id=agent_id,
        request_headers=request_headers,
    )
    session_data = {
        "session_id": session_id,
        "agent_id": agent_id,
        "conversation_id": deps.conversation_id,
        "run_id": deps.run_id,
    }

    stream_run = None

    async def event_generator():
        nonlocal stream_run
        active_run = ctx.active_runs.register(
            agent_id=deps.agent_id,
            session_id=session_id,
            user_id=deps.user,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
        )
        stream_run = active_run
        live_key = (deps.agent_id, session_id, deps.user)
        try:
            # Capture the durable prefix before this Run writes its first fact.
            # Observer history/replay can then exclude any later concurrent Run.
            # Failure of this optional observation read must not rerun or abort
            # an otherwise valid request; normal persistence still fails closed.
            try:
                baseline = await ctx.store.get_event_head(session_id, agent_id=deps.agent_id)
            except Exception as exc:
                logger.warning("Live observation unavailable error_type={}", type(exc).__name__)
            else:
                ctx.session_live.begin(live_key, deps.run_id, baseline_sequence=baseline)
            # The client may expose its stop action as soon as this event arrives.
            # Register the cancellable run first so an immediate stop cannot race
            # asynchronous history/input preparation and incorrectly return 404.
            ctx.session_live.publish(live_key, deps.run_id, "session", session_data)
            yield {"event": "session", "data": json.dumps(session_data)}
            async with aclosing(runner.run_stream(
                session_id,
                message,
                deps,
                attachments=body.attachments,
                context_refs=body.context_refs,
                deferred_tool_results=deferred_tool_results,
                branch_from=body.branch_from,
                persister=ctx.event_persister,
                active_run=active_run,
            )) as stream:
                async for ev in stream:
                    ctx.session_live.publish(live_key, deps.run_id, ev["type"], ev)
                    yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}
        except asyncio.CancelledError:
            active_run.observe_cancellation(transport_cancel_reason())
            raise
        except GeneratorExit:
            active_run.observe_cancellation("stream_generator_closed")
            raise
        finally:
            active_run.cancellation_token.cancel()
            ctx.active_runs.unregister(active_run)
            # Ensure background persistence finishes before the client refreshes session list.
            try:
                with anyio.move_on_after(5, shield=True):
                    try:
                        await ctx.event_persister.flush(session_id, agent_id=agent_id)
                    except Exception as exc:
                        # Runner reports write failures before its terminal frame.
                        logger.error("Stream cleanup persistence failed session={} error={}",
                                     session_id, type(exc).__name__)
            finally:
                ctx.session_live.end(live_key, deps.run_id)

    return DiagnosticEventSourceResponse(
        event_generator(), fields={**session_data, "agent_name": getattr(runner, "agent_name", None)},
        active_run=lambda: stream_run if ctx.active_runs.get(
            agent_id=deps.agent_id, session_id=session_id,
        ) is stream_run else None,
    )
