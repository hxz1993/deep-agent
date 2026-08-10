from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from sse_starlette.sse import EventSourceResponse

from deep_agent.agent.deferred import build_deferred_tool_results
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.attachments import UnsupportedAttachmentModalityError
from deep_agent.agent.context.compaction import ContextWindowExceededError
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.agent.run_identity import conversation_id_for, new_run_id
from deep_agent.api.v1.deps import build_runner, build_runner_for_agent
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.core.exceptions import AgentNotFoundError, LlmModelNotFoundError
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
    )


async def _ensure_session_access(
    ctx: AppContextDep,
    session_id: str,
    *,
    user: str,
    agent_id: str | None,
) -> None:
    owner = await ctx.store.get_session_owner(session_id, agent_id=agent_id)
    if owner.exists and owner.user_id != user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


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
    if body.branch_from is not None and has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_from and deferred_tool_results are mutually exclusive.",
        )
    if has_context_refs and has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="context_refs and deferred_tool_results are mutually exclusive.",
        )
    if body.branch_from is not None and body.branch_from < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_from must be non-negative.",
        )


async def _validate_branch_from(
    ctx: AppContextDep,
    session_id: str,
    *,
    agent_id: str | None,
    branch_from: int | None,
) -> None:
    if branch_from is None:
        return
    history = await ctx.store.get_history(session_id, agent_id=agent_id)
    if branch_from > len(history):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"branch_from {branch_from} out of range (history length {len(history)}).",
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
    user: RequestUserDep,
) -> ChatResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    request_headers = extract_forward_headers(request)
    if body.session_id is not None:
        await _ensure_session_access(ctx, session_id, user=user, agent_id=None)
    await _validate_branch_from(ctx, session_id, agent_id=None, branch_from=body.branch_from)
    try:
        runner = await build_runner(
            ctx,
            model=body.model if body.model_id is None else None,
            model_id=body.model_id,
            temperature=body.temperature,
            capabilities=body.capabilities,
            request_headers=request_headers,
        )
    except LlmModelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    deps = _agent_deps(
        ctx,
        session_id=session_id,
        user=user,
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
    except (ContextWindowExceededError, UnsupportedAttachmentModalityError) as exc:
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
    user: RequestUserDep,
) -> EventSourceResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    deferred_tool_results = _deferred_results(body)
    message = body.message or ""
    request_headers = extract_forward_headers(request)

    api_agent_id: str | None = None
    if body.agent_id is not None:
        try:
            agent_id = normalize_agent_id(body.agent_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        try:
            runner = await build_runner_for_agent(
                ctx,
                agent_id,
                model_id=body.model_id,
                request_headers=request_headers,
            )
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
        api_agent_id = agent_id
    else:
        try:
            runner = await build_runner(
                ctx,
                model=body.model if body.model_id is None else None,
                model_id=body.model_id,
                temperature=body.temperature,
                capabilities=body.capabilities,
                request_headers=request_headers,
            )
        except LlmModelNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        deps = _agent_deps(
            ctx,
            session_id=session_id,
            user=user,
            request_headers=request_headers,
        )
        session_data = {
            "session_id": session_id,
            "conversation_id": deps.conversation_id,
            "run_id": deps.run_id,
        }
        api_agent_id = None

    if body.session_id is not None:
        await _ensure_session_access(
            ctx,
            session_id,
            user=user,
            agent_id=api_agent_id,
        )
    await _validate_branch_from(ctx, session_id, agent_id=api_agent_id, branch_from=body.branch_from)

    async def event_generator():
        active_run = ctx.active_runs.register(
            agent_id=deps.agent_id,
            session_id=session_id,
            user_id=deps.user,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
        )
        try:
            # The client may expose its stop action as soon as this event arrives.
            # Register the cancellable run first so an immediate stop cannot race
            # asynchronous history/input preparation and incorrectly return 404.
            yield {"event": "session", "data": json.dumps(session_data)}
            async for ev in runner.run_stream(
                session_id,
                message,
                deps,
                attachments=body.attachments,
                context_refs=body.context_refs,
                deferred_tool_results=deferred_tool_results,
                branch_from=body.branch_from,
                persister=ctx.event_persister,
                active_run=active_run,
            ):
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}
        finally:
            active_run.cancellation_token.cancel()
            ctx.active_runs.unregister(active_run)
            # Ensure background persistence finishes before the client refreshes session list.
            await ctx.event_persister.flush(session_id, agent_id=api_agent_id)

    return EventSourceResponse(event_generator())
