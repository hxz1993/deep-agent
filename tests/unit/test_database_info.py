from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class DatabaseInfoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        stores, self.engine, session_factory = make_sqlite_stores_sync()

        async def seed_revision() -> None:
            async with session_factory() as session:
                await session.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                    ),
                )
                await session.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES ('020')"),
                )
                await session.commit()

        asyncio.run(seed_revision())
        init_context(
            AppContext.for_testing(
                stores=stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            ),
        )
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_returns_actual_database_schema_revision(self) -> None:
        response = self.client.get(
            "/api/v1/database-info",
            headers={"user": "test-user"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"schema_version": "020"})

    def test_requires_authenticated_user(self) -> None:
        self.assertEqual(self.client.get("/api/v1/database-info").status_code, 401)


if __name__ == "__main__":
    unittest.main()
