"""Validate and convert App Skill ownership in revision 015 data."""

from collections import defaultdict
from uuid import UUID

from deep_agent.integrations.app_scope import (
    APP_SKILL_SCOPE_PREFIX,
    GENERAL_SKILL_SCOPE,
    INTEGRATION,
    LEGACY_SKILL_SCOPE_PREFIX,
    app_skill_scope,
    app_skill_storage_key,
    matches_legacy_skill_key,
)
from deep_agent.integrations.catalog import get_integration_definition
from deep_agent.services.integration_app_access import IntegrationAppAccessError, app_space


class AppSkillDataError(ValueError):
    pass


def app_skill_data_updates(configs, packages, agent_skills=()):
    """Return changes only after the complete graph has passed validation."""
    config_scopes = {}
    config_keys = set()
    settings_updates = {}
    inferred = defaultdict(list)
    references = {}
    for row in configs:
        try:
            space_id, app_id = row["scope_key"].split("/")
            settings = row["settings"]
            key = row["integration_code"], row["scope_key"]
            if (
                get_integration_definition(row["integration_code"]) is None
                or row["scope_type"] != "app"
                or str(UUID(row["id"])) != row["id"] or app_space(app_id) != space_id
                or not row["tenant_id"] or len(row["tenant_id"]) > 191
                or not isinstance(settings, dict) or set(settings) not in ({"prompt_append"}, {"prompt_append", "skills"})
                or not isinstance(settings["prompt_append"], str) or len(settings["prompt_append"]) > 4000
                or key in config_keys
            ):
                raise ValueError
            if "skills" in settings:
                names = settings["skills"]
                if (not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names)
                        or len(names) != len(set(names))):
                    raise ValueError
                references[row["id"]] = names
            elif row["integration_code"] != INTEGRATION:
                raise ValueError
        except (KeyError, TypeError, ValueError, IntegrationAppAccessError) as exc:
            raise AppSkillDataError("Invalid application configuration") from exc
        config_keys.add(key)
        config_scopes[row["id"]] = app_skill_scope(app_id)

    scope_updates = {}
    app_packages = set()
    scoped_codes = set()
    for row in packages:
        scope = row.get("scope_ref")
        if scope in (None, GENERAL_SKILL_SCOPE):
            continue
        code = row["code"]
        manifest = row["manifest"]
        name = manifest.get("code") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not name or not isinstance(scope, str):
            raise AppSkillDataError("Invalid application Skill identity")
        if scope.startswith(LEGACY_SKILL_SCOPE_PREFIX):
            config_id = scope.removeprefix(LEGACY_SKILL_SCOPE_PREFIX)
            if config_id not in config_scopes or config_id in references:
                raise AppSkillDataError("Legacy Skill has no matching legacy application configuration")
            if not matches_legacy_skill_key(code, name, config_id=config_id):
                raise AppSkillDataError("Invalid legacy application Skill identity")
            scope = config_scopes[config_id]
            scope_updates[code] = scope
            inferred[config_id].append(name)
        elif scope.startswith(APP_SKILL_SCOPE_PREFIX):
            app_id = scope.removeprefix(APP_SKILL_SCOPE_PREFIX)
            try:
                app_space(app_id)
            except IntegrationAppAccessError as exc:
                raise AppSkillDataError("Invalid application Skill owner") from exc
            if code != app_skill_storage_key(app_id, name) and not matches_legacy_skill_key(code, name):
                raise AppSkillDataError("Invalid application Skill identity")
        else:
            raise AppSkillDataError("Unknown application Skill scope")
        if (scope, name) in app_packages:
            raise AppSkillDataError("Duplicate Skill codes in an application")
        app_packages.add((scope, name))
        scoped_codes.add(code)

    for row in configs:
        config_id = row["id"]
        if config_id not in references:
            settings_updates[config_id] = {**row["settings"], "skills": sorted(inferred[config_id])}
        elif any((config_scopes[config_id], name) not in app_packages for name in references[config_id]):
            raise AppSkillDataError("Application configuration references a missing or out-of-scope Skill")
    if any(scoped_codes.intersection(names or ()) for names in agent_skills):
        raise AppSkillDataError("An Agent references an application Skill directly")
    return scope_updates, settings_updates
