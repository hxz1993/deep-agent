from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from deep_agent.db.serialization import serialize_message
from maintenance.session_history_backup import (
    default_output_root,
    export_session_history_backup,
)


class SessionHistoryBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_exports_verified_legacy_session_archive(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            source = root / "source.db"
            output = root / "backups"
            await self._create_source(source)

            result = await export_session_history_backup(
                self._url(source),
                environment="test",
                output_root=output,
                exported_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=timezone.utc),
                git_commit="abc123",
            )

            self.assertEqual(
                result.path.name,
                "deep-agent-sessions-test-v007-20260826-010203.db",
            )
            self.assertEqual(result.row_counts["chat_sessions"], 1)
            self.assertEqual(result.row_counts["chat_messages"], 1)
            self.assertEqual(result.row_counts["chat_context_checkpoints"], 1)
            self.assertEqual(result.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result.checksum_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result.path.parent.stat().st_mode & 0o777, 0o700)

            expected_digest = hashlib.sha256(result.path.read_bytes()).hexdigest()
            self.assertEqual(
                result.checksum_path.read_text(encoding="ascii"),
                f"{expected_digest}  {result.path.name}\n",
            )
            with sqlite3.connect(result.path) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone(),
                    ("ok",),
                )
                self.assertEqual(
                    connection.execute("SELECT version_num FROM alembic_version").fetchone(),
                    ("007",),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT message_count, max_sequence, has_checkpoint "
                        "FROM _session_manifest"
                    ).fetchone(),
                    (1, 0, 1),
                )
                artifact = connection.execute(
                    "SELECT kind, handle, availability FROM _external_artifacts"
                ).fetchone()
                self.assertEqual(artifact, ("platform_file", "file-42", "not_checked"))
                info = connection.execute(
                    "SELECT format_version, environment, alembic_revision, git_commit, "
                    "table_manifest FROM _backup_info"
                ).fetchone()
                self.assertEqual(info[:4], ("1.0", "test", "007", "abc123"))
                table_manifest = json.loads(str(info[4]))
                self.assertEqual(
                    [item["row_count"] for item in table_manifest["tables"]],
                    [1, 1, 1],
                )
                self.assertEqual(table_manifest["relationship_anomaly_count"], 0)

            self.assertFalse(list(output.rglob("*.partial-TMP-to-delete*")))

    async def test_failed_export_removes_partial_files(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            source = root / "source.db"
            output = root / "backups"
            with sqlite3.connect(source) as connection:
                connection.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
                connection.execute("INSERT INTO alembic_version VALUES ('006')")

            with self.assertRaises(ValueError):
                await export_session_history_backup(
                    self._url(source),
                    environment="test",
                    output_root=output,
                )

            self.assertFalse(
                [path for path in output.rglob("*") if path.is_file()]
                if output.exists()
                else []
            )

    async def test_preserves_and_marks_orphan_messages(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            source = root / "source.db"
            output = root / "backups"
            await self._create_source(source)
            with sqlite3.connect(source) as connection:
                payload = serialize_message(
                    ModelRequest(parts=[UserPromptPart(content="orphan")])
                )
                connection.execute(
                    "INSERT INTO chat_messages VALUES "
                    "(2,'agent-1','missing-session',0,?,?)",
                    (json.dumps(payload), "2026-08-26 01:00:00"),
                )

            result = await export_session_history_backup(
                self._url(source),
                environment="test",
                output_root=output,
                exported_at=datetime(2026, 8, 26, 2, 0, 0, tzinfo=timezone.utc),
                git_commit="abc123",
            )

            with sqlite3.connect(result.path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_messages"
                    ).fetchone(),
                    (2,),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT source_session_present, message_count "
                        "FROM _session_manifest WHERE session_id='missing-session'"
                    ).fetchone(),
                    (0, 1),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT anomaly_type, source_table FROM _backup_anomalies"
                    ).fetchone(),
                    ("missing_session", "chat_messages"),
                )

    def test_default_output_root_is_separate_from_config_backups(self) -> None:
        self.assertEqual(default_output_root().name, "deep-agent-session-backups")

    async def _create_source(self, path: Path) -> None:
        engine = create_async_engine(self._url(path))
        payload = serialize_message(
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "hello",
                            {
                                "kind": "attachment-ref",
                                "schema_version": 1,
                                "id": "attachment-1",
                                "name": "demo.txt",
                                "mime": "text/plain",
                                "platform_file_id": "file-42",
                            },
                        ]
                    )
                ]
            )
        )
        try:
            async with engine.begin() as connection:
                for statement in (
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
                    "INSERT INTO alembic_version VALUES ('007')",
                    """CREATE TABLE chat_sessions (
                        agent_id VARCHAR(36) NOT NULL,
                        session_id VARCHAR(191) NOT NULL,
                        user_id VARCHAR(128), title TEXT,
                        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                        PRIMARY KEY (agent_id, session_id)
                    )""",
                    """CREATE TABLE chat_messages (
                        id BIGINT NOT NULL PRIMARY KEY,
                        agent_id VARCHAR(36) NOT NULL,
                        session_id VARCHAR(191) NOT NULL,
                        sequence INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        created_at DATETIME NOT NULL,
                        UNIQUE (agent_id, session_id, sequence)
                    )""",
                    """CREATE TABLE chat_context_checkpoints (
                        agent_id VARCHAR(36) NOT NULL,
                        session_id VARCHAR(191) NOT NULL,
                        source_last_sequence INTEGER NOT NULL,
                        source_history_hash VARCHAR(64) NOT NULL,
                        payload TEXT NOT NULL,
                        payload_schema_version INTEGER NOT NULL,
                        projection_fingerprint VARCHAR(64) NOT NULL,
                        estimated_tokens BIGINT NOT NULL,
                        summary_model_id VARCHAR(36), row_version BIGINT NOT NULL,
                        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                        PRIMARY KEY (agent_id, session_id)
                    )""",
                ):
                    await connection.execute(text(statement))
                now = "2026-08-26 01:00:00"
                await connection.execute(
                    text(
                        "INSERT INTO chat_sessions VALUES "
                        "('agent-1','session-1','user-1','Demo',:now,:now)"
                    ),
                    {"now": now},
                )
                await connection.execute(
                    text(
                        "INSERT INTO chat_messages VALUES "
                        "(1,'agent-1','session-1',0,:payload,:now)"
                    ),
                    {"payload": json.dumps(payload), "now": now},
                )
                await connection.execute(
                    text(
                        "INSERT INTO chat_context_checkpoints VALUES "
                        "('agent-1','session-1',0,'history-hash',:payload,1,"
                        "'projection-hash',123,NULL,1,:now,:now)"
                    ),
                    {"payload": json.dumps([payload]), "now": now},
                )
        finally:
            await engine.dispose()

    @staticmethod
    def _url(path: Path) -> str:
        return f"sqlite+aiosqlite:///{path.as_posix()}"


if __name__ == "__main__":
    unittest.main()
