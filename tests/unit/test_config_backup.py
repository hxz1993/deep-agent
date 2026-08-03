from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from deep_agent.db.models import (
    Agent,
    AuxiliaryModelConfig,
    Base,
    ChatContextCheckpoint,
    ChatMessage,
    ChatSession,
    LlmModel,
    McpServer,
    ModelCatalog,
    UiToolDefinition,
)
from maintenance.config_backup import (
    CONFIG_TABLES,
    SESSION_TABLES,
    database_url_from_config,
    default_output_root,
    export_backup,
    repository_root,
)


class ConfigBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_exports_one_sqlite_database_with_config_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            source_path = root / "source.db"
            output_root = root / "backups"
            await self._create_source(source_path)

            result = await export_backup(
                self._url(source_path),
                environment="test",
                output_root=output_root,
                exported_at=datetime(2026, 7, 23, 13, 14, 15, tzinfo=timezone.utc),
                git_commit="abc123",
            )

            self.assertEqual(
                result.path.name,
                "deep-agent-config-test-v001-20260723-131415.db",
            )
            self.assertEqual(
                [path.resolve() for path in (output_root / "test").iterdir()],
                [result.path.resolve()],
            )
            self.assertEqual(result.revision, "001")
            self.assertEqual(result.row_counts["agents"], 1)
            self.assertEqual(result.row_counts["chat_sessions"], 0)
            self.assertEqual(result.row_counts["chat_messages"], 0)
            self.assertEqual(result.row_counts["chat_context_checkpoints"], 0)

            with sqlite3.connect(result.path) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    set(CONFIG_TABLES)
                    | set(SESSION_TABLES)
                    | {"alembic_version", "_backup_info"}
                    <= tables
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchone(),
                    ("001",),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT api_key, context_window_tokens, max_output_tokens, tags "
                        "FROM llm_models WHERE id = 'model-1'"
                    ).fetchone(),
                    ("plain-api-key", 128000, 8192, '["reasoning","tool_call"]'),
                )
                catalog_row = connection.execute(
                    "SELECT input_modalities, output_modalities, capabilities, release_date, "
                    "model_metadata, provider_metadata "
                    "FROM model_catalog WHERE provider_id = 'openai' "
                    "AND provider_model_id = 'gpt-test'"
                ).fetchone()
                self.assertIsNotNone(catalog_row)
                self.assertEqual(json.loads(str(catalog_row[0])), ["text", "image"])
                self.assertEqual(json.loads(str(catalog_row[1])), ["text"])
                self.assertEqual(
                    json.loads(str(catalog_row[2])),
                    {"reasoning": True, "tool_call": True},
                )
                self.assertEqual(
                    catalog_row[3],
                    "2026-01-01",
                )
                self.assertEqual(
                    json.loads(str(catalog_row[4])),
                    {"release_date": "2026-01-01"},
                )
                self.assertEqual(
                    json.loads(str(catalog_row[5])),
                    {"adapter": "openai"},
                )
                mcp_config = connection.execute(
                    "SELECT config FROM mcp_servers WHERE name = 'remote'"
                ).fetchone()
                self.assertIsNotNone(mcp_config)
                self.assertEqual(
                    json.loads(str(mcp_config[0])),
                    {
                        "url": "http://example.test/mcp",
                        "headers": {"token": "plain-token"},
                    },
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM chat_sessions").fetchone(),
                    (0,),
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone(),
                    (0,),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_context_checkpoints"
                    ).fetchone(),
                    (0,),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT environment, source_dialect, alembic_revision, "
                        "git_commit FROM _backup_info"
                    ).fetchone(),
                    ("test", "sqlite", "001", "abc123"),
                )

    async def test_failed_export_removes_partial_database(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            source_path = root / "broken.db"
            output_root = root / "backups"
            with sqlite3.connect(source_path) as connection:
                connection.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32))"
                )
                connection.execute(
                    "INSERT INTO alembic_version (version_num) VALUES ('001')"
                )

            with self.assertRaises(Exception):
                await export_backup(
                    self._url(source_path),
                    environment="test",
                    output_root=output_root,
                    exported_at=datetime(2026, 7, 23, 13, 14, 15, tzinfo=timezone.utc),
                    git_commit="abc123",
                )

            generated = list(output_root.rglob("*")) if output_root.exists() else []
            self.assertFalse([path for path in generated if path.is_file()])

    def test_default_output_root_is_next_to_repository(self) -> None:
        self.assertEqual(
            default_output_root().name,
            "deep-agent-config-backups",
        )
        self.assertEqual(
            default_output_root().parent,
            repository_root().parent,
        )

    def test_reads_sqlite_database_from_external_config(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            config_path = root / "backup.env"
            config_path.write_text(
                "DEEP_AGENT_SQLITE_PATH=source.db\n",
                encoding="utf-8",
            )
            self.assertEqual(
                database_url_from_config(config_path),
                f"sqlite+aiosqlite:///{(root / 'source.db').resolve().as_posix()}",
            )

    async def _create_source(self, path: Path) -> None:
        engine = create_async_engine(self._url(path))
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO alembic_version (version_num) VALUES ('001')"
                    )
                )
                await connection.execute(
                    insert(LlmModel.__table__),
                    {
                        "id": "model-1",
                        "name": "model",
                        "model": "openai:test",
                        "api_key": "plain-api-key",
                        "input_modalities": ["text"],
                        "context_window_tokens": 128000,
                        "max_output_tokens": 8192,
                        "tags": ["reasoning", "tool_call"],
                    },
                )
                await connection.execute(
                    insert(ModelCatalog.__table__),
                    {
                        "provider_id": "openai",
                        "provider_model_id": "gpt-test",
                        "provider_label": "OpenAI",
                        "provider_npm": "@ai-sdk/openai",
                        "provider_api_url": "https://api.openai.com/v1",
                        "provider_doc_url": "https://platform.openai.com/docs",
                        "lab_id": "openai",
                        "lab_label": "OpenAI",
                        "lab_description": "OpenAI lab",
                        "canonical_model_id": "openai/gpt-test",
                        "model_label": "GPT Test",
                        "model_description": "Test model",
                        "family": "gpt",
                        "release_date": "2026-01-01",
                        "canonical_context_window_tokens": 128000,
                        "canonical_max_output_tokens": 8192,
                        "context_window_tokens": 128000,
                        "max_output_tokens": 8192,
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                        "capabilities": {"reasoning": True, "tool_call": True},
                        "status": "active",
                        "lab_metadata": {"country": "US"},
                        "model_metadata": {"release_date": "2026-01-01"},
                        "provider_metadata": {"adapter": "openai"},
                        "source": "models_dev",
                        "refreshed_at": datetime(
                            2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc
                        ),
                    },
                )
                await connection.execute(
                    insert(McpServer.__table__),
                    {
                        "name": "remote",
                        "transport": "url",
                        "config": {
                            "url": "http://example.test/mcp",
                            "headers": {"token": "plain-token"},
                        },
                        "enabled": True,
                    },
                )
                await connection.execute(
                    insert(Agent.__table__),
                    {
                        "id": "agent-1",
                        "name": "agent",
                        "model_id": "model-1",
                        "capabilities": ["mcp:remote"],
                        "prompt": "test",
                    },
                )
                await connection.execute(
                    insert(UiToolDefinition.__table__),
                    {
                        "name": "manual_tool",
                        "description": "manual",
                        "parameters_json_schema": {"type": "object"},
                        "source": "manual",
                        "enabled": True,
                    },
                )
                await connection.execute(
                    insert(AuxiliaryModelConfig.__table__),
                    {
                        "use_case": "title",
                        "llm_model_id": "model-1",
                    },
                )
                await connection.execute(
                    insert(ChatSession.__table__),
                    {
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                        "user_id": "user-1",
                    },
                )
                await connection.execute(
                    insert(ChatMessage.__table__),
                    {
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                        "sequence": 1,
                        "payload": {"kind": "request"},
                    },
                )
                await connection.execute(
                    insert(ChatContextCheckpoint.__table__),
                    {
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                        "source_last_sequence": 1,
                        "source_history_hash": "a" * 64,
                        "payload": [{"kind": "request", "parts": []}],
                        "payload_schema_version": 1,
                        "projection_fingerprint": "b" * 64,
                        "estimated_tokens": 12,
                        "row_version": 1,
                    },
                )
        finally:
            await engine.dispose()

    @staticmethod
    def _url(path: Path) -> str:
        return f"sqlite+aiosqlite:///{path.as_posix()}"


if __name__ == "__main__":
    unittest.main()
