from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.database_info import build_database_info
from deep_agent.core.context import AppContext, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class DatabaseInfoBuilderTests(unittest.TestCase):
    def test_sqlite_only_exposes_file_name(self) -> None:
        info = build_database_info("sqlite+aiosqlite:////private/data/deep-agent.db")

        self.assertEqual(info.database_type, "sqlite")
        self.assertEqual(info.database_type_label, "SQLite")
        self.assertEqual(info.connection_mode, "本地文件")
        self.assertEqual(info.database, "deep-agent.db")
        self.assertNotIn("/private/data", info.model_dump_json())

    def test_mysql_omits_credentials_and_query(self) -> None:
        info = build_database_info(
            "mysql+asyncmy://secret-user:secret-password@test-db:3307/deep_agent?charset=utf8mb4"
        )
        payload = info.model_dump_json()

        self.assertEqual(info.database_type, "mysql")
        self.assertEqual(info.database_type_label, "MySQL")
        self.assertEqual(info.connection_mode, "远程数据库")
        self.assertEqual(info.host, "test-db")
        self.assertEqual(info.port, 3307)
        self.assertEqual(info.database, "deep_agent")
        self.assertEqual(info.connection_summary, "MySQL · test-db:3307/deep_agent")
        self.assertNotIn("secret-user", payload)
        self.assertNotIn("secret-password", payload)
        self.assertNotIn("charset=utf8mb4", payload)


class DatabaseInfoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        stores, self.engine, session_factory = make_sqlite_stores_sync()

        async def seed_revision() -> None:
            async with session_factory() as session:
                await session.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                    )
                )
                await session.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES ('002')")
                )
                await session.commit()

        asyncio.run(seed_revision())
        init_context(
            AppContext.for_testing(
                stores=stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            )
        )

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_returns_safe_read_only_connection_summary(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        client = TestClient(app)

        response = client.get(
            "/api/v1/database-info",
            headers={"user": "test-user"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            set(response.json()),
            {
                "schema_version",
                "database_type",
                "database_type_label",
                "connection_mode",
                "host",
                "port",
                "database",
                "connection_summary",
                "credentials_hidden",
                "charset",
            },
        )
        self.assertEqual(response.json()["schema_version"], "002")
        self.assertTrue(response.json()["credentials_hidden"])
        self.assertEqual(response.json()["charset"]["status"], "ok")
        self.assertEqual(response.json()["charset"]["database"], "UTF-8")

    def test_requires_authenticated_user(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        client = TestClient(app)

        self.assertEqual(client.get("/api/v1/database-info").status_code, 401)

    def test_lightweight_summary_executes_only_revision_sql(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        statements = []

        def record_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", record_sql)
        try:
            with patch("deep_agent.api.v1.database_info.read_database_charset") as charset_check:
                with TestClient(app) as client:
                    response = client.get("/api/v1/database-info?include_charset=false", headers={"user": "test-user"})
                charset_check.assert_not_called()
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record_sql)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["schema_version"], "002")
        self.assertIsNone(response.json()["charset"])
        self.assertEqual(statements, ["SELECT version_num FROM alembic_version"])

    def test_explicit_charset_check_retains_existing_result_and_sql(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        statements = []

        def record_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", record_sql)
        try:
            with TestClient(app) as client:
                response = client.get("/api/v1/database-info?include_charset=true", headers={"user": "test-user"})
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record_sql)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["charset"]["status"], "ok")
        self.assertIn("PRAGMA encoding", statements)

    def test_invalid_charset_flag_is_rejected_and_auth_remains_required(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/database-info?include_charset=false").status_code, 401)
            invalid = client.get("/api/v1/database-info?include_charset=invalid", headers={"user": "test-user"})
        self.assertEqual(invalid.status_code, 422)

    def test_both_modes_retain_revision_unavailable_error(self) -> None:
        async def remove_revision() -> None:
            async with self.engine.begin() as connection:
                await connection.execute(text("DELETE FROM alembic_version"))

        asyncio.run(remove_revision())
        app = FastAPI()
        app.include_router(api_router)
        with TestClient(app) as client:
            for include_charset in ("false", "true"):
                with self.subTest(include_charset=include_charset):
                    response = client.get(f"/api/v1/database-info?include_charset={include_charset}", headers={"user": "test-user"})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["detail"], "Database schema version is unavailable")


if __name__ == "__main__":
    unittest.main()
