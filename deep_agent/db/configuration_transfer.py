from __future__ import annotations

from typing import Any, Sequence

from deep_agent.db.models import Base


SESSION_TABLES = (
    "chat_sessions",
    "chat_session_events",
)
BACKUP_SCOPE_SCHEMA_VERSION = "1.1"


def configuration_table_names() -> tuple[str, ...]:
    """Return configuration tables in foreign-key-safe insertion order."""

    return tuple(
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in SESSION_TABLES
    )


def backup_scope_manifest(copied_tables: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": BACKUP_SCOPE_SCHEMA_VERSION,
        "copied_tables": list(copied_tables),
        "structure_only_tables": list(SESSION_TABLES),
        "external_files": [
            {
                "kind": "uploaded_skill_packages",
                "included": False,
                "locator_table": "skill_packages",
                "locator_column": "platform_file_id",
                "export_api": "/api/v1/skills/export",
            }
        ],
    }


def validate_table_classification() -> None:
    structure_only = set(SESSION_TABLES)
    model_tables = set(Base.metadata.tables)
    unknown = structure_only - model_tables
    if unknown:
        raise ValueError(
            "Backup session table exclusions are stale: "
            + ", ".join(sorted(unknown))
        )


__all__ = [
    "BACKUP_SCOPE_SCHEMA_VERSION",
    "SESSION_TABLES",
    "backup_scope_manifest",
    "configuration_table_names",
    "validate_table_classification",
]
