from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy import text

from deep_agent.channel_gateway.config import GatewayConfig
from deep_agent.channel_gateway.runtime import ChannelRuntime
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.factory import close_db, create_engine
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.url import get_database_url


@dataclass
class GatewayContext:
    config: GatewayConfig
    runtime: ChannelRuntime
    engine: object


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
            if revision != "002":
                raise RuntimeError(
                    f"Channel Gateway requires database revision 002, found {revision or 'none'}"
                )
            stores = create_data_stores(resolve_dialect(database_url), session_factory)
            assert stores.channel is not None
            runtime = ChannelRuntime(stores.channel, config)
            app.state.gateway = GatewayContext(config, runtime, engine)
            await runtime.start()
            yield
        finally:
            context = getattr(app.state, "gateway", None)
            if context is not None:
                await context.runtime.stop()
            await close_db(engine)

    app = FastAPI(title="DeepAgent Channel Gateway", version="0.7.0", lifespan=lifespan)

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
        return {"status": "ok", "database_revision": "002"}

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

    return app


app = create_gateway_app()


__all__ = ["app", "create_gateway_app"]
