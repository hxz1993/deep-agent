from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db import migrate
from deep_agent.db.event_codec import encode_messages
from maintenance.channel_peer_metadata import repair_sqlite_peer_metadata


class ChannelPeerMetadataRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TMP_to_delete-peer-metadata-")
        self.path = Path(self.tmp.name) / "fixture.db"
        url = f"sqlite+aiosqlite:///{self.path}"
        with patch("deep_agent.db.migrate.get_database_url", return_value=url):
            config = migrate._build_alembic_config()
        config.attributes["database_url"] = url
        # This maintenance command repairs the historical 002 layout only.
        command.upgrade(config, "002")
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("ALTER TABLE channel_session_bindings DROP COLUMN peer_metadata")
            db.execute("ALTER TABLE channel_session_bindings ADD COLUMN peer_display_name TEXT")
            agent_id = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
            db.execute("""INSERT INTO channel_accounts
                (id,name,bot_id,secret,agent_id,session_user_id,settings)
                VALUES ('account','fixture','fixture-bot','test-only-placeholder',?,'fixture-user','{}')""", (agent_id,))
            for index, (peer_type, name) in enumerate((("single", "小明"), ("group", None), ("single", "小红"))):
                sid = f"session-{index}"
                title = f"旧渠道标题-{index}"
                db.execute("""INSERT INTO chat_sessions
                    (session_id,agent_id,user_id,title,next_sequence) VALUES (?,?,'fixture-user',?,4)""", (sid, agent_id, title))
                db.execute("""INSERT INTO channel_session_bindings
                    (id,account_id,peer_type,peer_id,session_id,peer_display_name)
                    VALUES (?,'account',?,?,?,?)""", (f"binding-{index}", peer_type, f"peer-{index}", sid, name))
                payload = {"title": title, "source": "channel", "source_event_id": None, "channel": {
                    "account_id": "account", "account_name": "fixture", "channel_type": "wecom",
                    "peer_type": peer_type, "peer_id": f"peer-{index}", "peer_display_name": name,
                }}
                db.execute("""INSERT INTO chat_session_events
                    (event_id,session_id,sequence,schema_version,event_type,payload)
                    VALUES (?,?,1,1,'session/title',?)""", (f"title-{index}", sid, json.dumps(payload, ensure_ascii=False)))
                message = encode_messages([ModelRequest(parts=[UserPromptPart(content="保留这段用户正文")])], session_id=sid, run_id=f"agent-run-{index}")[0]
                db.execute("""INSERT INTO chat_session_events
                    (event_id,session_id,sequence,schema_version,event_type,payload)
                    VALUES (?,?,2,1,'user/message',?)""", (message.event_id, sid, json.dumps(message.payload, ensure_ascii=False)))
                if index == 2:
                    title = "用户人工改名"
                    db.execute("UPDATE chat_sessions SET title=? WHERE session_id=?", (title, sid))
                    db.execute("""INSERT INTO chat_session_events
                        (event_id,session_id,sequence,schema_version,event_type,payload)
                        VALUES ('manual-title',?,3,1,'session/title',?)""", (sid, json.dumps({"title": title, "source": "user"})))
                db.execute("""INSERT INTO channel_message_runs
                    (id,account_id,binding_id,external_message_id,session_id,payload_hash,processing_status,delivery_status)
                    VALUES (?,'account',?,?,?,'hash','completed','sent')""", (f"run-{index}", f"binding-{index}", f"message-{index}", sid))

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in (
                "channel_session_bindings", "channel_message_runs", "chat_sessions", "chat_session_events", "alembic_version",
            )}

    def test_dryrun_then_apply_preserves_history_ids_manual_title_and_is_idempotent(self):
        before = self.snapshot()
        report = repair_sqlite_peer_metadata(self.path)
        self.assertEqual(report, {"schema_changed": True, "bindings_changed": 3, "origins_changed": 3, "titles_changed": 2, "applied": False})
        self.assertEqual(before, self.snapshot())
        applied = repair_sqlite_peer_metadata(self.path, apply=True)
        backup = Path(applied["backup"])
        self.assertTrue(backup.is_file())
        self.assertIn("TMP_to_delete", backup.name)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as db:
            self.assertIn("peer_display_name", {row[1] for row in db.execute("PRAGMA table_info(channel_session_bindings)")})
        after = self.snapshot()
        self.assertEqual(after["channel_message_runs"], before["channel_message_runs"])
        self.assertEqual(after["alembic_version"], before["alembic_version"])
        for old, new in zip(before["channel_session_bindings"], after["channel_session_bindings"]):
            name = old.pop("peer_display_name")
            metadata = json.loads(new.pop("peer_metadata"))
            expected = {"chat_id": old["peer_id"]} if old["peer_type"] == "group" else {"user_id": old["peer_id"]}
            if name:
                expected["display_name"] = name
            self.assertEqual(metadata, expected)
            self.assertEqual(old, new)
        titles = [row["title"] for row in after["chat_sessions"]]
        self.assertEqual(titles, ["User｜小明(peer-0)", "Group｜peer-1", "用户人工改名"])
        for old, new in zip(before["chat_sessions"], after["chat_sessions"]):
            old.pop("title")
            new.pop("title")
            self.assertEqual(old, new)
        for old, new in zip(before["chat_session_events"], after["chat_session_events"]):
            if old["event_type"] == "user/message" or json.loads(old["payload"]).get("source") == "user":
                self.assertEqual(old, new)
            else:
                origin = json.loads(new["payload"])["channel"]
                self.assertIn("peer_metadata", origin)
                self.assertNotIn("peer_display_name", origin)
                old.pop("payload")
                new.pop("payload")
                self.assertEqual(old, new)
        unchanged = self.snapshot()
        second = repair_sqlite_peer_metadata(self.path, apply=True)
        self.assertFalse(second["schema_changed"])
        self.assertEqual([second[key] for key in ("bindings_changed", "origins_changed", "titles_changed")], [0, 0, 0])
        self.assertEqual(unchanged, self.snapshot())

    def test_conflicting_legacy_peer_types_fail_before_ddl(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""INSERT INTO channel_session_bindings
                (id,account_id,peer_type,peer_id) VALUES ('conflict','account','userid','peer-0')""")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "conflicting bindings"):
            repair_sqlite_peer_metadata(self.path, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.path.parent.glob("*TMP_to_delete*.db")), [])

    def test_transaction_rolls_back_ddl_and_data_after_mid_apply_failure(self):
        import maintenance.channel_peer_metadata as repair
        original_rows = repair._rows
        calls = 0
        def fail_verification(db, table, key):
            nonlocal calls
            calls += 1
            # Two preflights read four tables each; the ninth call verifies the
            # already changed physical schema, before transaction commit.
            if calls == 9:
                raise RuntimeError("fixture verification failure")
            return original_rows(db, table, key)
        before = self.snapshot()
        with patch.object(repair, "_rows", side_effect=fail_verification):
            with self.assertRaisesRegex(RuntimeError, "fixture verification failure"):
                repair_sqlite_peer_metadata(self.path, apply=True)
        self.assertEqual(self.snapshot(), before)
        with sqlite3.connect(self.path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(channel_session_bindings)")}
            self.assertIn("peer_display_name", columns)
            self.assertNotIn("peer_metadata", columns)

    def add_orphan_event(self):
        # Simulate an unrelated pre-existing legacy integrity defect, without
        # changing or deleting an existing Session or Event.
        with sqlite3.connect(self.path) as db:
            db.execute("""INSERT INTO chat_session_events
                (event_id,session_id,sequence,schema_version,event_type,payload)
                SELECT 'orphan-title','missing-old-session',1,schema_version,event_type,payload
                FROM chat_session_events WHERE event_id='title-0'""")

    def test_explicit_orphan_preservation_is_readonly_for_orphan_payload_and_idempotent(self):
        self.add_orphan_event()
        before = self.snapshot()
        with sqlite3.connect(self.path) as db:
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
        with self.assertRaisesRegex(ValueError, "foreign key violations"):
            repair_sqlite_peer_metadata(self.path, apply=True)
        self.assertEqual(list(self.path.parent.glob("*TMP_to_delete*.db")), [])
        dry = repair_sqlite_peer_metadata(self.path, preserve_orphan_events=True)
        self.assertEqual(dry["preserved_orphan_events"], 1)
        self.assertEqual(before, self.snapshot())
        applied = repair_sqlite_peer_metadata(self.path, apply=True, preserve_orphan_events=True)
        self.assertEqual(applied["preserved_orphan_events"], 1)
        after = self.snapshot()
        old_orphan = next(row for row in before["chat_session_events"] if row["event_id"] == "orphan-title")
        new_orphan = next(row for row in after["chat_session_events"] if row["event_id"] == "orphan-title")
        self.assertEqual(new_orphan, old_orphan)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), violations)
        second = repair_sqlite_peer_metadata(self.path, apply=True, preserve_orphan_events=True)
        self.assertEqual(second["origins_changed"], 0)
        self.assertEqual(self.snapshot(), after)

    def test_new_foreign_key_violation_rolls_back_even_when_preserving_old_orphans(self):
        import maintenance.channel_peer_metadata as repair
        self.add_orphan_event()
        before = self.snapshot()
        original_rows = repair._rows
        calls = 0

        def inject_foreign_key_violation(db, table, key):
            nonlocal calls
            calls += 1
            if calls == 9:
                # Deliberately introduce a new deferred violation after the
                # two preflights, in a table outside the four row snapshots.
                db.execute("PRAGMA defer_foreign_keys=ON")
                db.execute("CREATE TABLE repair_fk_probe (session_id TEXT REFERENCES chat_sessions(session_id))")
                db.execute("INSERT INTO repair_fk_probe VALUES ('new-missing-session')")
            return original_rows(db, table, key)

        with patch.object(repair, "_rows", side_effect=inject_foreign_key_violation):
            with self.assertRaisesRegex(ValueError, "integrity verification failed"):
                repair_sqlite_peer_metadata(self.path, apply=True, preserve_orphan_events=True)
        self.assertEqual(self.snapshot(), before)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='repair_fk_probe'").fetchone())
            columns = {row[1] for row in db.execute("PRAGMA table_info(channel_session_bindings)")}
            self.assertIn("peer_display_name", columns)
            self.assertNotIn("peer_metadata", columns)

    def test_preserve_orphans_does_not_allow_other_existing_foreign_key_defects(self):
        self.add_orphan_event()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE repair_fk_probe (session_id TEXT REFERENCES chat_sessions(session_id))")
            db.execute("INSERT INTO repair_fk_probe VALUES ('other-missing-session')")
        with self.assertRaisesRegex(ValueError, "outside orphan events"):
            repair_sqlite_peer_metadata(self.path, apply=True, preserve_orphan_events=True)
        self.assertEqual(list(self.path.parent.glob("*TMP_to_delete*.db")), [])


if __name__ == "__main__":
    unittest.main()
