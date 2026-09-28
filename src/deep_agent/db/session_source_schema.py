"""Physical schema portion of the unpublished 010 migration."""
import sqlalchemy as sa
from alembic.operations import Operations

SOURCE_COLUMNS = {"source_type": 64, "source_id": 255}

def ensure_session_source_columns(operations: Operations) -> None:
    existing = {item["name"]: item for item in sa.inspect(operations.get_bind()).get_columns("chat_sessions")}
    # Validate all existing fields before executing any DDL.
    for name, length in SOURCE_COLUMNS.items():
        column = existing.get(name)
        if column is not None and (
            not isinstance(column["type"], sa.String)
            or column["type"].length != length
            or not column["nullable"]
            or column.get("default") is not None
        ):
            raise RuntimeError(f"010 session source schema mismatch: chat_sessions.{name}")
    for name, length in SOURCE_COLUMNS.items():
        if name not in existing:
            operations.add_column("chat_sessions", sa.Column(name, sa.String(length), nullable=True))
