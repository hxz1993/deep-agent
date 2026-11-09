"""Identifiers shared by application configuration, Skill storage, and runtime."""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import or_

from deep_agent.integrations.catalog import IntegrationCode

INTEGRATION = IntegrationCode.DEEPTABLE_ASSISTANT.value
GENERAL_SKILL_SCOPE = "general"
_SCOPE_PREFIX = "integration-scope:"


def app_scope_key(app_id: str, space_id: str) -> str:
    return f"{space_id}/{app_id}"


def skill_scope_ref(config_id: str) -> str:
    return _SCOPE_PREFIX + config_id


def scoped_skill_code(scope_ref: str, package_code: str) -> str:
    digest = sha256(package_code.encode("utf-8")).hexdigest()
    # Double hyphens are excluded from original Skill names by the ZIP parser.
    return f"app--{scope_ref.removeprefix(_SCOPE_PREFIX)}--{digest}"


def skill_package_code(row) -> str:
    if getattr(row, "scope_ref", None) in (None, GENERAL_SKILL_SCOPE):
        return row.code
    return row.manifest["code"]


def skill_scope_condition(column, scope_ref: str):
    if scope_ref == GENERAL_SKILL_SCOPE:
        return or_(column.is_(None), column == GENERAL_SKILL_SCOPE)
    return column == scope_ref
