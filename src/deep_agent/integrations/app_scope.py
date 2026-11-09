"""Identifiers shared by application configuration, Skill storage, and runtime."""

from __future__ import annotations

import re
from hashlib import sha256

from sqlalchemy import or_

from deep_agent.integrations.catalog import IntegrationCode

INTEGRATION = IntegrationCode.DEEPTABLE_ASSISTANT.value
GENERAL_SKILL_SCOPE = "general"
_SCOPE_PREFIX = "integration-scope:"
_ASCII_PACKAGE_CODE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def app_scope_key(app_id: str, space_id: str) -> str:
    return f"{space_id}/{app_id}"


def skill_scope_ref(config_id: str) -> str:
    return _SCOPE_PREFIX + config_id


def scoped_skill_storage_keys(scope_ref: str, package_code: str) -> tuple[str, ...]:
    """Return the key for new installs, followed by any historical alternative."""
    config_id = scope_ref.removeprefix(_SCOPE_PREFIX)
    digest = sha256(package_code.encode("utf-8")).hexdigest()
    legacy = f"app--{config_id}--{digest}"
    if len(package_code) <= 64 and _ASCII_PACKAGE_CODE.fullmatch(package_code):
        # Keep the formats disjoint even when a package name is itself a SHA-256 string.
        return (f"app-skill--{config_id}--{package_code}", legacy)
    # Unicode names can collide under MySQL/filesystem collation or exceed a path component.
    return (legacy,)


def skill_package_code(row) -> str:
    if getattr(row, "scope_ref", None) in (None, GENERAL_SKILL_SCOPE):
        return row.code
    return row.manifest["code"]


def skill_scope_condition(column, scope_ref: str):
    if scope_ref == GENERAL_SKILL_SCOPE:
        return or_(column.is_(None), column == GENERAL_SKILL_SCOPE)
    return column == scope_ref
