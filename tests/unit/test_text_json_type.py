from __future__ import annotations

import unittest

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select

from deep_agent.db.types import JSONType, TextJSON


class TextJSONTypeTests(unittest.TestCase):
    def test_roundtrip_scalar_and_nested_values(self) -> None:
        metadata = MetaData()
        table = Table(
            "json_probe",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("payload", JSONType, nullable=False),
        )
        engine = create_engine("sqlite://", future=True)
        metadata.create_all(engine)

        sample = {
            "list": ["native", "mcp:echo"],
            "config": {"env": {"KEY": "value"}, "enabled": True},
            "count": 3,
        }
        with engine.begin() as conn:
            conn.execute(table.insert().values(id=1, payload=sample))

        with engine.connect() as conn:
            row = conn.execute(select(table.c.payload).where(table.c.id == 1)).scalar_one()

        self.assertEqual(row, sample)

    def test_nullable_column(self) -> None:
        metadata = MetaData()
        table = Table(
            "json_nullable",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("capabilities", JSONType, nullable=True),
        )
        engine = create_engine("sqlite://", future=True)
        metadata.create_all(engine)

        with engine.begin() as conn:
            conn.execute(table.insert().values(id=1, capabilities=None))

        with engine.connect() as conn:
            value = conn.execute(select(table.c.capabilities).where(table.c.id == 1)).scalar_one()

        self.assertIsNone(value)

    def test_json_type_alias(self) -> None:
        self.assertIs(JSONType, TextJSON)


if __name__ == "__main__":
    unittest.main()
