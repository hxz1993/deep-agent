from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.dialects.mysql import LONGTEXT


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
