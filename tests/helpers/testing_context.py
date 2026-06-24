from __future__ import annotations

from deep_agent.core.context import AppContext, init_context
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


def init_test_context(**kwargs) -> AppContext:
    stores, engine, session_factory = make_sqlite_stores_sync()
    ctx = AppContext.for_testing(
        stores=stores,
        db_engine=engine,
        db_session_factory=session_factory,
        **kwargs,
    )
    init_context(ctx)
    return ctx
