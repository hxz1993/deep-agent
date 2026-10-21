from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from sqlalchemy.exc import SQLAlchemyError

from deep_agent.db.charset import business_text_columns, mysql_charset_info, read_database_charset


class MySQLCharsetTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(database="utf8mb4", client="utf8mb4", connection="utf8mb4", results="utf8mb4")
        self.columns = [{"table_name": table, "column_name": column, "character_set_name": "utf8mb4"}
                        for table, column in sorted(business_text_columns())]

    def test_database_connection_and_all_business_fields_are_checked(self):
        info = mysql_charset_info(self.settings, self.columns)
        self.assertEqual(info.status, "ok")
        self.assertEqual(info.checked_columns, len(self.columns))
        self.assertIn(("chat_session_events", "payload"), business_text_columns())
        self.assertIn(("chat_session_events", "search_text"), business_text_columns())
        self.assertIn(("chat_sessions", "title"), business_text_columns())
        self.assertIn(("agents", "prompt"), business_text_columns())
        self.assertNotIn(("agents", "id"), business_text_columns())

    def test_utf8mb4_defaults_do_not_hide_an_old_payload_column(self):
        next(row for row in self.columns if (row["table_name"], row["column_name"]) == ("chat_session_events", "payload"))["character_set_name"] = "utf8"
        info = mysql_charset_info(self.settings, self.columns)
        self.assertEqual(info.status, "warning")
        self.assertTrue(any("chat_session_events.payload" in issue for issue in info.issues))

    def test_good_fields_do_not_hide_result_or_input_conversion(self):
        for field in ("database", "client", "connection", "results"):
            with self.subTest(field=field):
                info = mysql_charset_info({**self.settings, field: "utf8mb3"}, self.columns)
                self.assertEqual(info.status, "warning")
                self.assertEqual(len(info.issues), 1)

    def test_disabled_result_conversion_is_compatible(self):
        self.assertEqual(mysql_charset_info({**self.settings, "results": None}, self.columns).status, "ok")

    def test_missing_field_metadata_is_not_healthy(self):
        self.assertEqual(mysql_charset_info(self.settings, []).status, "unknown")
        info = mysql_charset_info(self.settings, self.columns[:-1])
        self.assertNotEqual(info.status, "ok")
        self.assertTrue(any("未能检查字段" in issue for issue in info.issues))

    def test_other_application_tables_and_opaque_ids_are_ignored(self):
        rows = self.columns + [{"table_name": "other_product", "column_name": "payload", "character_set_name": "latin1"},
                               {"table_name": "agents", "column_name": "id", "character_set_name": "ascii"}]
        self.assertEqual(mysql_charset_info(self.settings, rows).status, "ok")


class DatabaseCharsetFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_failure_is_safe_and_explicit(self):
        for failure in (SQLAlchemyError("secret connection credentials"), TimeoutError("secret SQL")):
            with self.subTest(type=type(failure).__name__):
                info = await read_database_charset(AsyncMock(execute=AsyncMock(side_effect=failure)), "mysql")
                self.assertEqual(info.status, "unknown")
                self.assertNotIn("secret", info.model_dump_json())

    async def test_unknown_engine_is_not_reported_healthy(self):
        info = await read_database_charset(AsyncMock(), "other")
        self.assertEqual(info.status, "not_applicable")


if __name__ == "__main__":
    unittest.main()
