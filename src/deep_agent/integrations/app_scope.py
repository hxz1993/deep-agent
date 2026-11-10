"""Identifiers shared by application configuration, Skill storage, and runtime."""

from __future__ import annotations

import re
from hashlib import sha256
from uuid import UUID

from sqlalchemy import or_

from deep_agent.integrations.catalog import IntegrationCode

INTEGRATION = IntegrationCode.DEEPTABLE_ASSISTANT.value
GENERAL_SKILL_SCOPE = "general"
APP_SKILL_SCOPE_PREFIX = "app:"
LEGACY_SKILL_SCOPE_PREFIX = "integration-scope:"
_ASCII_PACKAGE_CODE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def app_scope_key(app_id: str, space_id: str) -> str:
    return f"{space_id}/{app_id}"


def app_skill_scope(app_id: str) -> str:
    return APP_SKILL_SCOPE_PREFIX + app_id


def app_skill_storage_key(app_id: str, package_code: str) -> str:
    if len(package_code) <= 64 and _ASCII_PACKAGE_CODE.fullmatch(package_code):
        return f"app-pkg--{app_id}--{package_code}"
    # Hash non-ASCII names to avoid filesystem and database collation collisions.
    return f"app-pkg-hash--{app_id}--{sha256(package_code.encode('utf-8')).hexdigest()}"


def matches_legacy_skill_key(code: str, package_code: str, *, config_id: str | None = None) -> bool:
    prefix, separator, tail = code.partition("--")
    old_id, _, _ = tail.partition("--")
    if not separator or (config_id is not None and config_id != old_id):
        return False
    try:
        if str(UUID(old_id)) != old_id:
            return False
    except ValueError:
        return False
    if prefix == "app":
        return code == f"app--{old_id}--{sha256(package_code.encode('utf-8')).hexdigest()}"
    return (
        prefix == "app-skill" and len(package_code) <= 64
        and _ASCII_PACKAGE_CODE.fullmatch(package_code) is not None
        and code == f"app-skill--{old_id}--{package_code}"
    )


def skill_package_code(row) -> str:
    if getattr(row, "scope_ref", None) in (None, GENERAL_SKILL_SCOPE):
        return row.code
    return row.manifest["code"]


def skill_scope_condition(column, scope_ref: str):
    if scope_ref == GENERAL_SKILL_SCOPE:
        return or_(column.is_(None), column == GENERAL_SKILL_SCOPE)
    return column == scope_ref
