from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db import migrate
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.migrate import upgrade_database_sync
from deep_agent.db.stores.bundle import create_data_stores


EXPECTED_TABLES = {
    "agents",
    "auxiliary_model_configs",
    "chat_context_checkpoints",
    "chat_messages",
    "chat_sessions",
    "llm_models",
    "llm_providers",
    "mcp_servers",
    "model_catalog",
    "skill_packages",
    "ui_tool_definitions",
    "ui_tool_packages",
}


class SqliteBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrate_and_create_stores(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "bootstrap.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    await asyncio.to_thread(upgrade_database_sync)

            engine, session_factory = create_engine(url)
            try:
                stores = create_data_stores(DbDialect.SQLITE, session_factory)
                self.assertIsNotNone(stores.agent)
                self.assertIsNotNone(stores.llm_model)
                self.assertIsNotNone(stores.model_catalog)
                self.assertIsNotNone(stores.mcp)
                self.assertIsNotNone(stores.ui_tool)
                self.assertIsNotNone(stores.context_checkpoint)
                self.assertIsNotNone(stores.skill_package)

                await stores.coordinator.append(
                    "sess-bootstrap",
                    [ModelRequest(parts=[UserPromptPart(content="hello")])],
                )
                history = await stores.coordinator.get_history("sess-bootstrap")
                self.assertEqual(len(history), 1)
            finally:
                await engine.dispose()

    async def test_single_baseline_upgrade_downgrade_upgrade(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "baseline.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "head")
                    self._assert_current_baseline(db_path)

                    await asyncio.to_thread(command.downgrade, config, "base")
                    self.assertEqual(self._business_tables(db_path), set())

                    await asyncio.to_thread(command.upgrade, config, "head")
                    self._assert_current_baseline(db_path)

    async def test_007_infers_merges_and_round_trips_providers(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "providers.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "006")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "UPDATE llm_models SET base_url=?, api_key=?",
                            ("https://api.openai.com/v1", "shared-key"),
                        )
                        connection.execute(
                            "INSERT INTO llm_models "
                            "(id,name,model,base_url,api_key,input_modalities,"
                            "context_window_tokens,max_output_tokens,tags,created_at,updated_at) "
                            "SELECT 'model-copy','copy','openai:gpt-copy',base_url,api_key,"
                            "input_modalities,context_window_tokens,max_output_tokens,tags,"
                            "created_at,updated_at FROM llm_models LIMIT 1"
                        )
                        connection.execute(
                            "INSERT INTO llm_models "
                            "(id,name,model,base_url,api_key,input_modalities,"
                            "context_window_tokens,max_output_tokens,tags,created_at,updated_at) "
                            "SELECT 'model-custom','custom','openai:custom',"
                            "'https://kimi.example/v1','custom-key',input_modalities,"
                            "context_window_tokens,max_output_tokens,tags,created_at,updated_at "
                            "FROM llm_models LIMIT 1"
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "007")
                    self.assertEqual(self._row_count(db_path, "llm_providers"), 2)
                    self.assertNotIn("base_url", self._columns(db_path, "llm_models"))
                    self.assertNotIn("api_key", self._columns(db_path, "llm_models"))
                    self.assertIn(
                        ("provider_id", "llm_providers", "id", "RESTRICT"),
                        self._foreign_keys(db_path, "llm_models"),
                    )
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "INSERT INTO llm_providers "
                            "(id,name,provider_type,use_default_base_url,base_url,api_key,enabled) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (
                                "provider-runtime-create",
                                "runtime-create",
                                "openai_compatible",
                                0,
                                "https://runtime-create.example/v1",
                                "runtime-key",
                                1,
                            ),
                        )
                        timestamps = connection.execute(
                            "SELECT created_at, updated_at FROM llm_providers WHERE id=?",
                            ("provider-runtime-create",),
                        ).fetchone()
                        connection.commit()
                    self.assertIsNotNone(timestamps)
                    self.assertTrue(timestamps[0])
                    self.assertTrue(timestamps[1])
                    self.assertEqual(
                        self._row_count_where(
                            db_path,
                            "llm_models",
                            "provider_id=(SELECT provider_id FROM llm_models WHERE id=?)",
                            ("model-copy",),
                        ),
                        2,
                    )
                    self.assertEqual(
                        self._scalar(
                            db_path,
                            "SELECT provider_type FROM llm_providers WHERE id="
                            "(SELECT provider_id FROM llm_models WHERE id='model-custom')",
                        ),
                        "openai_compatible",
                    )

                    await asyncio.to_thread(command.downgrade, config, "006")
                    self.assertNotIn("provider_id", self._columns(db_path, "llm_models"))
                    self.assertNotIn("llm_providers", self._business_tables(db_path))
                    self.assertEqual(
                        self._scalar(db_path, "SELECT api_key FROM llm_models WHERE id='model-custom'"),
                        "custom-key",
                    )
                    await asyncio.to_thread(command.upgrade, config, "007")
                    self.assertEqual(self._revision(db_path), "007")

    async def test_002_groups_ui_tools_and_rewrites_agent_packages(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "tool-packages.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "001")
                    with sqlite3.connect(db_path) as connection:
                        for name in ("deep_cube__clarify", "deep_cube__config_fill"):
                            connection.execute(
                                "INSERT INTO ui_tool_definitions "
                                "(name, description, parameters_json_schema, source, enabled) "
                                "VALUES (?, 'DeepCube', '{}', 'manual', 1)",
                                (name,),
                            )
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (
                                json.dumps(
                                    [
                                        "native",
                                        "python-sandbox",
                                        "attachment",
                                        "ui:deep_cube__clarify",
                                        "ui:builtin__confirm",
                                        "mcp:deepcube",
                                    ]
                                ),
                            ),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "002")
                    self.assertEqual(self._row_count(db_path, "ui_tool_packages"), 1)
                    self.assertEqual(
                        self._scalar(
                            db_path,
                            "SELECT package_code FROM ui_tool_definitions "
                            "WHERE name = 'deep_cube__clarify'",
                        ),
                        "ui:deepcube",
                    )
                    capabilities = json.loads(
                        str(
                            self._scalar(
                                db_path,
                                "SELECT capabilities FROM agents WHERE name = 'default'",
                            )
                        )
                    )
                    self.assertEqual(
                        capabilities,
                        [
                            "builtin:general",
                            "builtin:sandbox",
                            "builtin:attachment",
                            "ui:deepcube",
                            "ui:builtin",
                            "mcp:deepcube",
                        ],
                    )

                    await asyncio.to_thread(command.downgrade, config, "001")
                    downgraded = json.loads(
                        str(
                            self._scalar(
                                db_path,
                                "SELECT capabilities FROM agents WHERE name = 'default'",
                            )
                        )
                    )
                    self.assertEqual(
                        downgraded,
                        [
                            "native",
                            "python-sandbox",
                            "attachment",
                            "ui:deep_cube__clarify",
                            "ui:deep_cube__config_fill",
                            "ui:builtin__confirm",
                            "ui:builtin__choice",
                            "mcp:deepcube",
                        ],
                    )
                    await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "002")

    async def test_002_rejects_unmapped_ui_data_before_ddl(self) -> None:
        cases = (
            ("unknown-tool", "INSERT INTO ui_tool_definitions "
             "(name, description, parameters_json_schema, source, enabled) "
             "VALUES ('other__tool', 'Other', '{}', 'manual', 1)"),
            ("unknown-reference", "UPDATE agents SET capabilities = '[\"ui:other__tool\"]' "
             "WHERE name = 'default'"),
            ("missing-deepcube-reference", "UPDATE agents SET capabilities = "
             "'[\"ui:deep_cube__clarify\"]' WHERE name = 'default'"),
        )
        for case_name, statement in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                    db_path = Path(tmpdir) / f"{case_name}.db"
                    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
                    with patch("deep_agent.db.url.get_database_url", return_value=url):
                        with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                            config = migrate._build_alembic_config()
                            await asyncio.to_thread(command.upgrade, config, "001")
                            with sqlite3.connect(db_path) as connection:
                                connection.execute(statement)
                                connection.commit()
                            with self.assertRaises(RuntimeError):
                                await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "001")
                    self.assertNotIn("ui_tool_packages", self._business_tables(db_path))
                    self.assertNotIn("package_code", self._columns(db_path, "ui_tool_definitions"))

    async def test_002_accepts_pre_release_package_codes_in_001_data(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "pre-release-package-codes.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "001")
                    with sqlite3.connect(db_path) as connection:
                        for name in ("deep_cube__clarify", "deep_cube__config_fill"):
                            connection.execute(
                                "INSERT INTO ui_tool_definitions "
                                "(name, description, parameters_json_schema, source, enabled) "
                                "VALUES (?, 'DeepCube', '{}', 'manual', 1)",
                                (name,),
                            )
                        expected = [
                            "builtin:general",
                            "builtin:sandbox",
                            "ui:deepcube",
                            "ui:builtin",
                            "mcp:deepcube",
                        ]
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (json.dumps(expected),),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "002")

                    self.assertEqual(self._revision(db_path), "002")
                    capabilities = json.loads(
                        str(
                            self._scalar(
                                db_path,
                                "SELECT capabilities FROM agents WHERE name = 'default'",
                            )
                        )
                    )
                    self.assertEqual(capabilities, expected)
                    self.assertEqual(self._row_count(db_path, "ui_tool_packages"), 1)

    async def test_003_rejects_legacy_skill_capabilities_before_ddl(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "legacy-skill.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "002")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (json.dumps(["builtin:general", "skill:calculator"]),),
                        )
                        connection.commit()
                    with self.assertRaisesRegex(RuntimeError, "legacy skill"):
                        await asyncio.to_thread(command.upgrade, config, "003")
                    self.assertEqual(self._revision(db_path), "002")
                    self.assertNotIn("skills", self._columns(db_path, "agents"))
                    self.assertNotIn("skill_packages", self._business_tables(db_path))

    async def test_003_downgrade_refuses_non_empty_skill_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "skill-downgrade.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "003")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "UPDATE agents SET skills = ? WHERE name = 'default'",
                            (json.dumps(["deepagent-diagnostics"]),),
                        )
                        connection.commit()
                    with self.assertRaisesRegex(RuntimeError, "agents.skills"):
                        await asyncio.to_thread(command.downgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "003")

                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "UPDATE agents SET skills = '[]' WHERE name = 'default'"
                        )
                        connection.execute(
                            "INSERT INTO skill_packages "
                            "(code,label,description,schema_version,source,enabled,platform_file_id,"
                            "file_name,file_size,content_hash,manifest,resource_index,contains_scripts) "
                            "VALUES ('x','X','X','1.0','upload',1,'f','x.zip',1,?, '{}','[]',0)",
                            ("a" * 64,),
                        )
                        connection.commit()
                    with self.assertRaisesRegex(RuntimeError, "skill_packages"):
                        await asyncio.to_thread(command.downgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "003")

    async def test_004_removes_only_explicit_attachment_package_and_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "remove-attachment.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "003")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (
                                json.dumps(
                                    [
                                        "builtin:general",
                                        "builtin:attachment",
                                        "mcp:deepcube",
                                        "builtin:attachment",
                                    ]
                                ),
                            ),
                        )
                        connection.execute(
                            "INSERT INTO agents (id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('null-agent','null-agent',?,NULL,NULL,'[]','test')",
                            (
                                self._scalar(
                                    db_path,
                                    "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
                                ),
                            ),
                        )
                        connection.execute(
                            "INSERT INTO agents (id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('empty-agent','empty-agent',?,NULL,'[]','[]','test')",
                            (
                                self._scalar(
                                    db_path,
                                    "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
                                ),
                            ),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "004")
                    self.assertEqual(self._revision(db_path), "004")
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'default'",
                                )
                            )
                        ),
                        ["builtin:general", "mcp:deepcube"],
                    )
                    self.assertIsNone(
                        self._scalar(
                            db_path,
                            "SELECT capabilities FROM agents WHERE name = 'null-agent'",
                        )
                    )
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'empty-agent'",
                                )
                            )
                        ),
                        [],
                    )

                    await asyncio.to_thread(command.downgrade, config, "003")
                    self.assertEqual(self._revision(db_path), "003")
                    await asyncio.to_thread(command.upgrade, config, "004")
                    self.assertEqual(self._revision(db_path), "004")
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'default'",
                                )
                            )
                        ),
                        ["builtin:general", "mcp:deepcube"],
                    )

    async def test_005_removes_text_tools_server_and_agent_references(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "remove-text-tools.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "004")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "INSERT INTO mcp_servers "
                            "(name,transport,config,inspection,description,enabled) "
                            "VALUES ('text_tools','builtin','{}','{\"status\":\"not_applicable\"}',"
                            "'retired fixture',1)"
                        )
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (
                                json.dumps(
                                    [
                                        "mcp:text_tools",
                                        "builtin:general",
                                        "mcp:text_tools",
                                        "mcp:deepcube",
                                    ]
                                ),
                            ),
                        )
                        model_id = self._scalar(
                            db_path,
                            "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
                        )
                        connection.execute(
                            "INSERT INTO agents "
                            "(id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('null-agent','null-agent',?,NULL,NULL,'[]','test')",
                            (model_id,),
                        )
                        connection.execute(
                            "INSERT INTO agents "
                            "(id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('empty-agent','empty-agent',?,NULL,'[]','[]','test')",
                            (model_id,),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "005")
                    self.assertEqual(self._revision(db_path), "005")
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'default'",
                                )
                            )
                        ),
                        ["builtin:general", "mcp:deepcube"],
                    )
                    self.assertIsNone(
                        self._scalar(
                            db_path,
                            "SELECT capabilities FROM agents WHERE name = 'null-agent'",
                        )
                    )
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'empty-agent'",
                                )
                            )
                        ),
                        [],
                    )
                    self.assertEqual(
                        self._row_count_where(
                            db_path,
                            "mcp_servers",
                            "name = ?",
                            ("text_tools",),
                        ),
                        0,
                    )

                    await asyncio.to_thread(command.downgrade, config, "004")
                    self.assertEqual(self._revision(db_path), "004")
                    self.assertEqual(
                        self._row_count_where(
                            db_path,
                            "mcp_servers",
                            "name = ?",
                            ("text_tools",),
                        ),
                        0,
                    )
                    await asyncio.to_thread(command.upgrade, config, "005")
                    self.assertEqual(self._revision(db_path), "005")

    async def test_006_removes_doc_search_server_and_agent_references(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "remove-doc-search.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "005")
                    with sqlite3.connect(db_path) as connection:
                        connection.execute(
                            "INSERT INTO mcp_servers "
                            "(name,transport,config,inspection,description,enabled) "
                            "VALUES ('doc_search','builtin','{}','{\"status\":\"not_applicable\"}',"
                            "'retired fixture',1)"
                        )
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (
                                json.dumps(
                                    [
                                        "mcp:doc_search",
                                        "builtin:general",
                                        "mcp:doc_search",
                                        "mcp:deepcube",
                                    ]
                                ),
                            ),
                        )
                        model_id = self._scalar(
                            db_path,
                            "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
                        )
                        connection.execute(
                            "INSERT INTO agents "
                            "(id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('null-agent-006','null-agent-006',?,NULL,NULL,'[]','test')",
                            (model_id,),
                        )
                        connection.execute(
                            "INSERT INTO agents "
                            "(id,name,model_id,temperature,capabilities,skills,prompt) "
                            "VALUES ('empty-agent-006','empty-agent-006',?,NULL,'[]','[]','test')",
                            (model_id,),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "006")
                    self.assertEqual(self._revision(db_path), "006")
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'default'",
                                )
                            )
                        ),
                        ["builtin:general", "mcp:deepcube"],
                    )
                    self.assertIsNone(
                        self._scalar(
                            db_path,
                            "SELECT capabilities FROM agents WHERE name = 'null-agent-006'",
                        )
                    )
                    self.assertEqual(
                        json.loads(
                            str(
                                self._scalar(
                                    db_path,
                                    "SELECT capabilities FROM agents WHERE name = 'empty-agent-006'",
                                )
                            )
                        ),
                        [],
                    )
                    self.assertEqual(
                        self._row_count_where(
                            db_path,
                            "mcp_servers",
                            "name = ?",
                            ("doc_search",),
                        ),
                        0,
                    )

                    await asyncio.to_thread(command.downgrade, config, "005")
                    self.assertEqual(self._revision(db_path), "005")
                    self.assertEqual(
                        self._row_count_where(
                            db_path,
                            "mcp_servers",
                            "name = ?",
                            ("doc_search",),
                        ),
                        0,
                    )
                    await asyncio.to_thread(command.upgrade, config, "006")
                    self.assertEqual(self._revision(db_path), "006")

    def _assert_current_baseline(self, db_path: Path) -> None:
        self.assertEqual(self._revision(db_path), "007")
        self.assertEqual(self._business_tables(db_path), EXPECTED_TABLES)
        self.assertNotIn("max_requests", self._columns(db_path, "agents"))
        self.assertNotIn("capabilities_source", self._columns(db_path, "llm_models"))
        self.assertIn("provider_id", self._columns(db_path, "llm_models"))
        self.assertNotIn("base_url", self._columns(db_path, "llm_models"))
        self.assertNotIn("api_key", self._columns(db_path, "llm_models"))
        self.assertIn(
            ("provider_id", "llm_providers", "id", "RESTRICT"),
            self._foreign_keys(db_path, "llm_models"),
        )
        self.assertEqual(
            self._index_columns(db_path, "ix_llm_models_provider_id"),
            ["provider_id"],
        )
        self.assertGreaterEqual(self._row_count(db_path, "llm_providers"), 1)
        provider_id = str(
            self._scalar(db_path, "SELECT id FROM llm_providers ORDER BY name LIMIT 1")
        )
        self.assertEqual(len(provider_id), 32)
        self.assertNotIn("-", provider_id)
        self.assertEqual(
            self._scalar(
                db_path,
                "SELECT enabled FROM llm_providers WHERE id = ?",
                (provider_id,),
            ),
            0,
        )
        self.assertNotIn("resource_capabilities", self._columns(db_path, "mcp_servers"))
        self.assertIn("package_code", self._columns(db_path, "ui_tool_definitions"))
        self.assertIn("skills", self._columns(db_path, "agents"))
        self.assertEqual(
            json.loads(str(self._scalar(db_path, "SELECT skills FROM agents WHERE name = 'default'"))),
            [],
        )
        self.assertEqual(
            self._primary_key(db_path, "chat_context_checkpoints"),
            ["agent_id", "session_id"],
        )
        self.assertEqual(
            self._index_columns(db_path, "ix_chat_sessions_user_id_agent_id_updated_at"),
            ["user_id", "agent_id", "updated_at"],
        )
        self.assertEqual(self._row_count(db_path, "llm_models"), 1)
        self.assertEqual(self._row_count(db_path, "agents"), 1)
        self.assertEqual(self._row_count(db_path, "mcp_servers"), 1)
        self.assertEqual(self._row_count(db_path, "auxiliary_model_configs"), 1)
        self.assertEqual(
            self._scalar(
                db_path,
                "SELECT model_id FROM agents WHERE name = 'default'",
            ),
            self._scalar(
                db_path,
                "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
            ),
        )
        for name in ("deepcube",):
            raw = self._scalar(
                db_path,
                "SELECT inspection FROM mcp_servers WHERE name = ?",
                (name,),
            )
            self.assertEqual(json.loads(str(raw)), {"status": "not_applicable"})

    @staticmethod
    def _revision(db_path: Path) -> str:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        assert row is not None
        return str(row[0])

    @staticmethod
    def _business_tables(db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "AND name <> 'alembic_version'"
            ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _columns(db_path: Path, table_name: str) -> set[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    @staticmethod
    def _primary_key(db_path: Path, table_name: str) -> list[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(row[1]) for row in sorted(rows, key=lambda item: item[5]) if row[5]]

    @staticmethod
    def _index_columns(db_path: Path, index_name: str) -> list[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA index_info({index_name})").fetchall()
        return [str(row[2]) for row in rows]

    @staticmethod
    def _foreign_keys(db_path: Path, table_name: str) -> set[tuple[str, str, str, str]]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
        return {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in rows
        }

    @staticmethod
    def _row_count(db_path: Path, table_name: str) -> int:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _row_count_where(
        db_path: Path,
        table_name: str,
        where: str,
        parameters: tuple[object, ...],
    ) -> int:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE {where}",
                parameters,
            ).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _scalar(
        db_path: Path,
        statement: str,
        params: tuple[object, ...] = (),
    ) -> object:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(statement, params).fetchone()
        assert row is not None
        return row[0]


if __name__ == "__main__":
    unittest.main()
