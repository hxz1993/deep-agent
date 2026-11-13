from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config


class IntegrationAppMigrationTests(unittest.TestCase):
    def test_existing_014_upgrade_restart_and_guarded_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_integration_migration_") as tmp:
            path = Path(tmp) / "history.db"
            config = Config("alembic.ini")
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "014")
            with sqlite3.connect(path) as db:
                db.execute("""INSERT INTO skill_packages
                  (code,label,description,schema_version,source,platform_file_id,file_name,file_size,content_hash,manifest,resource_index)
                  VALUES ('legacy','Legacy','description','1.0','upload','file','legacy.zip',10,'hash','{}','[]')""")
            command.upgrade(config, "015")
            command.upgrade(config, "015")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("015",))
                self.assertEqual(db.execute("SELECT code,scope_ref FROM skill_packages").fetchall(), [("legacy", None)])
                db.execute("""INSERT INTO integration_scoped_settings
                  (id,integration_code,scope_type,scope_key,tenant_id,settings)
                  VALUES ('scope','deeptable-assistant','app','space/space001','tenant','{}')""")
            with self.assertRaisesRegex(RuntimeError, "would discard"):
                command.downgrade(config, "014")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("015",))
                db.execute("DELETE FROM integration_scoped_settings")
                db.execute("UPDATE skill_packages SET scope_ref='general'")
            command.downgrade(config, "014")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("014",))
                self.assertEqual(db.execute("SELECT code FROM skill_packages").fetchall(), [("legacy",)])
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
