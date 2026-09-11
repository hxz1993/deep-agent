from __future__ import annotations

from typing import Any, Sequence

from deep_agent.db.models import Base


SESSION_TABLES = (
    "chat_sessions",
    "chat_session_events",
    "channel_session_bindings",
    "channel_message_runs",
)
BACKUP_SCOPE_SCHEMA_VERSION = "1.1"
# Explicit logical dependency order. Keep this stable across 002/003: backup
# manifests are versioned assets and must not change with ORM FK declarations.
CONFIGURATION_TABLE_ORDER = (
    "global_settings", "llm_providers", "mcp_servers", "model_catalog",
    "skill_packages", "ui_tool_packages", "llm_models", "ui_tool_definitions",
    "agents", "channel_accounts",
)


def order_configuration_tables(table_names: Sequence[str]) -> tuple[str, ...]:
    names = set(table_names)
    unknown = names - set(CONFIGURATION_TABLE_ORDER)
    if unknown:
        raise ValueError("Configuration tables need a logical dependency order: "
                         + ", ".join(sorted(unknown)))
    return tuple(name for name in CONFIGURATION_TABLE_ORDER if name in names)


def configuration_table_names() -> tuple[str, ...]:
    """Return configuration tables in explicit logical insertion order."""

    return order_configuration_tables(
        tuple(name for name in Base.metadata.tables if name not in SESSION_TABLES)
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
    "order_configuration_tables",
    "validate_table_classification",
]
