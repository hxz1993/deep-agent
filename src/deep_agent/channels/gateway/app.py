from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy import text
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as StarletteHTTPException

from deep_agent.channels.gateway.webhook import WebhookMessage
from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.gateway.runtime import ChannelRuntime
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.factory import close_db, create_engine
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.url import get_database_url
from deep_agent.diagnostics.runtime import observe_lifespan
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.channels.operations import MessageOperationRequest
from deep_agent.channels.outbound import DeliveryError, PreparedComponent


class ResolveUsersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[Annotated[str, Field(min_length=1, max_length=191)]] = Field(max_length=500)
    timeout: float = Field(default=3.0, gt=0, le=10)


@dataclass
class GatewayContext:
    config: GatewayConfig
    runtime: ChannelRuntime
    engine: object
    database_revision: str | None


def create_gateway_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = GatewayConfig.from_env()
        database_url = get_database_url()
        engine, session_factory = create_engine(database_url)
        try:
            async with engine.connect() as connection:
                revision = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
            stores = create_data_stores(resolve_dialect(database_url), session_factory)
            assert stores.channel is not None
            runtime = ChannelRuntime(stores.channel, config)
            # API owns migrations; the revision is diagnostic metadata, not a
            # gateway-maintained version allowlist.
            app.state.gateway = GatewayContext(config, runtime, engine, revision)
            await runtime.start()
            yield
        finally:
            context = getattr(app.state, "gateway", None)
            if context is not None:
                await context.runtime.stop()
            await close_db(engine)

    app = FastAPI(title="DeepAgent Channel Gateway", version="0.7.0", lifespan=observe_lifespan(lifespan, "gateway"))

    async def authorize(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> GatewayContext:
        context: GatewayContext = request.app.state.gateway
        expected = f"Bearer {context.config.internal_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Gateway internal token",
            )
        return context

    @app.get("/health")
    async def health(context: GatewayContext = Depends(authorize)) -> dict:
        return {"status": "ok", "database_revision": context.database_revision}

    @app.get("/accounts/status")
    async def account_statuses(
        context: GatewayContext = Depends(authorize),
    ) -> dict:
        return context.runtime.status_payload()

    @app.post("/accounts/{account_id}/test")
    async def test_account(
        account_id: str,
        context: GatewayContext = Depends(authorize),
    ) -> dict:
        try:
            return await context.runtime.test_account(account_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Channel account not found") from exc

    @app.post("/accounts/{account_id}/users/resolve")
    async def resolve_users(
        account_id: str, body: ResolveUsersRequest,
        context: GatewayContext = Depends(authorize),
    ) -> dict:
        try:
            return await context.runtime.resolve_users(account_id, body.user_ids, timeout=body.timeout)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Channel account not found") from exc

    @app.post("/accounts/{account_id}/webhook")
    async def receive_webhook(account_id: str, body: WebhookMessage,
                              context: GatewayContext = Depends(authorize)) -> dict:
        try:
            return await context.runtime.receive_webhook(account_id, body)
        except LookupError:
            raise HTTPException(status_code=404, detail="Channel account not found") from None
        except ValueError:
            raise HTTPException(status_code=409, detail="Webhook message or configuration conflict") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Webhook message could not be accepted") from None

    @app.post("/messages/send")
    async def send_message(request: Request, context: GatewayContext = Depends(authorize)):
        # Enforce the limit while receiving, before multipart spools files.
        limit = max_upload_bytes() + 128 * 1024
        received = 0
        exceeded = False
        original_receive = request.receive

        async def bounded_receive():
            nonlocal received, exceeded
            message = await original_receive()
            received += len(message.get("body", b""))
            if received > limit:
                exceeded = True
                # MultipartParser closes its temporary files on this error.
                raise MultiPartException("Channel message body is too large")
            return message

        bounded = Request(request.scope, receive=bounded_receive)
        try:
            async with bounded.form(max_files=10, max_fields=1) as form:
                raw = form.get("metadata")
                if not isinstance(raw, str):
                    raise ValueError("Invalid metadata")
                command = MessageOperationRequest.model_validate_json(raw)
                prepared = []
                total = 0
                for index, component in enumerate(command.components):
                    data = None
                    if component.kind != "text":
                        uploaded = form.get(f"media_{index}")
                        if not isinstance(uploaded, UploadFile):
                            raise ValueError("Missing media")
                        data = await uploaded.read(max_upload_bytes() + 1)
                        total += len(data)
                        if total > max_upload_bytes():
                            raise HTTPException(status_code=413, detail="附件总大小超过上限")
                    prepared.append(PreparedComponent(**component.model_dump(), data=data))
                return await context.runtime.operations.send(command, prepared)
        except StarletteHTTPException:
            if exceeded:
                raise HTTPException(status_code=413, detail="附件总大小超过上限") from None
            raise
        except (ValidationError, ValueError):
            raise HTTPException(status_code=422, detail="无效的渠道消息请求") from None
        except DeliveryError as exc:
            raise HTTPException(status_code=503 if exc.unknown else 409, detail=str(exc)) from None

    return app


app = create_gateway_app()


__all__ = ["app", "create_gateway_app"]
