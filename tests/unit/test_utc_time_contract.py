from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy.dialects import mysql, postgresql, sqlite

from deep_agent.db.types import UtcTimestamp
from deep_agent.time import UtcDatetime, ensure_utc, utc_rfc3339


class _TimestampResponse(BaseModel):
    created_at: UtcDatetime


class UtcTimeContractTests(unittest.TestCase):
    def test_naive_and_offset_values_normalize_to_utc(self) -> None:
        naive = datetime(2026, 8, 31, 10, 23, 46)
        offset = datetime(
            2026,
            8,
            31,
            18,
            23,
            46,
            tzinfo=timezone(timedelta(hours=8)),
        )

        self.assertEqual(ensure_utc(naive), datetime(2026, 8, 31, 10, 23, 46, tzinfo=UTC))
        self.assertEqual(ensure_utc(offset), datetime(2026, 8, 31, 10, 23, 46, tzinfo=UTC))
        self.assertEqual(utc_rfc3339(offset), "2026-08-31T10:23:46+00:00")

    def test_public_timestamp_always_serializes_with_explicit_utc_offset(self) -> None:
        values = (
            "2026-08-31T10:23:46",
            "2026-08-31T10:23:46Z",
            "2026-08-31T18:23:46+08:00",
        )
        for value in values:
            with self.subTest(value=value):
                payload = _TimestampResponse(created_at=value).model_dump(mode="json")
                self.assertEqual(payload["created_at"], "2026-08-31T10:23:46+00:00")

    def test_database_type_preserves_utc_semantics_for_all_dialects(self) -> None:
        column = UtcTimestamp()
        source = datetime(
            2026,
            8,
            31,
            18,
            23,
            46,
            tzinfo=timezone(timedelta(hours=8)),
        )
        utc_value = datetime(2026, 8, 31, 10, 23, 46, tzinfo=UTC)

        for dialect in (sqlite.dialect(), mysql.dialect()):
            with self.subTest(dialect=dialect.name):
                bound = column.process_bind_param(source, dialect)
                self.assertEqual(bound, utc_value.replace(tzinfo=None))
                self.assertIsNone(bound.tzinfo)
                self.assertEqual(column.process_result_value(bound, dialect), utc_value)

        pg_dialect = postgresql.dialect()
        pg_bound = column.process_bind_param(source, pg_dialect)
        self.assertEqual(pg_bound, utc_value)
        self.assertEqual(column.process_result_value(pg_bound, pg_dialect), utc_value)

    def test_legacy_naive_database_value_is_read_as_utc(self) -> None:
        legacy = datetime(2026, 8, 31, 10, 23, 46)
        parsed = UtcTimestamp().process_result_value(legacy, sqlite.dialect())
        self.assertEqual(parsed, datetime(2026, 8, 31, 10, 23, 46, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
