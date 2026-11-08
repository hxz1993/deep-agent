from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config

from deep_agent.core.paths import REPO_ROOT


class ReasoningMigrationTests(unittest.TestCase):
    def test_013_upgrade_leaves_legacy_events_and_guards_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_reasoning_") as root:
            path = Path(root) / "fixture.db"
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "013")
            old_event = '{"message":{"kind":"request","parts":[{"part_kind":"user-prompt","content":"legacy"}]}}'
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO chat_sessions (session_id, agent_id, user_id, next_sequence) VALUES ('legacy', 'agent', 'owner', 2)")
                db.execute("""
                    INSERT INTO chat_session_events
                    (event_id,session_id,sequence,schema_version,event_type,payload,created_at,updated_at)
                    VALUES ('old','legacy',1,1,'user/message',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """, (old_event,))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alembic_version SET version_num='014'")
            with self.assertRaisesRegex(RuntimeError, "does not match this release"):
                command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alembic_version SET version_num='013'")
                db.execute("ALTER TABLE llm_models ADD COLUMN reasoning_options INTEGER")
            with self.assertRaisesRegex(RuntimeError, "unexpected column definition"):
                command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                self.assertNotIn("reasoning_effort", {item[1] for item in db.execute("PRAGMA table_info(agents)")})
                db.execute("ALTER TABLE llm_models DROP COLUMN reasoning_options")
                db.execute("ALTER TABLE llm_models ADD COLUMN reasoning_options TEXT")
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT payload FROM chat_session_events").fetchone(), (old_event,))
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("014",))
                db.execute("""
                    INSERT INTO llm_models (id,provider_id,name,model,reasoning_options)
                    VALUES ('synthetic','provider','synthetic','synthetic','{}')
                """)
            with self.assertRaisesRegex(RuntimeError, "discard saved reasoning"):
                command.downgrade(config, "013")
            with sqlite3.connect(path) as db:
                self.assertIn("reasoning_effort", {item[1] for item in db.execute("PRAGMA table_info(agents)")})
                db.execute("UPDATE llm_models SET reasoning_options=NULL")
            command.downgrade(config, "013")
            with sqlite3.connect(path) as db:
                self.assertNotIn("reasoning_options", {item[1] for item in db.execute("PRAGMA table_info(llm_models)")})
                self.assertEqual(db.execute("SELECT payload FROM chat_session_events").fetchone(), (old_event,))
