from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, TypeDecorator
from sqlalchemy.dialects.mysql import LONGTEXT

from deep_agent.time import ensure_utc


class TextJSON(TypeDecorator[Any]):
    """Store JSON-serializable values as text for broad database compatibility.

    Uses native TEXT/LONGTEXT instead of dialect JSON/JSONB types so the schema
    works on MySQL 5.6 and other engines without a JSON column type.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "mysql":
            return dialect.type_descriptor(LONGTEXT())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any | None, dialect) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value: Any | None, dialect) -> Any | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)


# Backward-compatible alias used by models and future migrations.
JSONType = TextJSON


class UtcTimestamp(TypeDecorator[datetime]):
    """Persist UTC consistently and always return an aware UTC datetime.

    PostgreSQL keeps a timezone-aware physical type. MySQL and SQLite store a
    naive UTC value because their datetime columns do not preserve offsets.
    Legacy naive values are interpreted as UTC when read or written.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        normalized = ensure_utc(value)
        if dialect.name == "postgresql":
            return normalized
        return normalized.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # type: ignore[no-untyped-def]
        del dialect
        return ensure_utc(value) if value is not None else None
