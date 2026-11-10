"""Convert existing App Skill data within database revision 015."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

from deep_agent.db.factory import create_engine
from deep_agent.db.models import Agent, IntegrationScopedSettings, SkillPackage
from deep_agent.db.stores.integration_app import IntegrationAppStore
from deep_agent.integrations.app_skill_data import AppSkillDataError, app_skill_data_updates
from maintenance.config_backup import database_url_from_config


async def convert_app_skill_ownership(database_url: str, *, apply: bool = False) -> tuple[int, int]:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and (not url.database or not Path(url.database).is_file()):
        raise AppSkillDataError("App Skill conversion requires an existing SQLite database")
    engine, factory = create_engine(database_url)
    store = IntegrationAppStore(factory)
    config = IntegrationScopedSettings.__table__
    package = SkillPackage.__table__
    transaction = store._write(exclusive=True) if apply else store._read()
    try:
        async with transaction as db:
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "015":
                raise AppSkillDataError("App Skill conversion requires database revision 015")
            configs = (await db.execute(select(
                config.c.id, config.c.integration_code, config.c.scope_type,
                config.c.scope_key, config.c.tenant_id, config.c.settings,
            ))).mappings().all()
            packages = (await db.execute(select(
                package.c.code, package.c.scope_ref, package.c.manifest,
            ).where(package.c.scope_ref.is_not(None)))).mappings().all()
            agent_skills = (await db.scalars(select(Agent.skills))).all()
            scopes, settings = app_skill_data_updates(configs, packages, agent_skills)
            if apply:
                for code, scope in scopes.items():
                    await db.execute(update(package).where(package.c.code == code).values(
                        scope_ref=scope, updated_at=package.c.updated_at,
                    ))
                for config_id, value in settings.items():
                    await db.execute(update(config).where(config.c.id == config_id).values(
                        settings=value, updated_at=config.c.updated_at,
                    ))
            return len(scopes), len(settings)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="External dotenv file with the target database configuration.")
    parser.add_argument("--apply", action="store_true", help="Apply the conversion after backup and writer shutdown.")
    args = parser.parse_args()
    try:
        skills, configs = asyncio.run(convert_app_skill_ownership(
            database_url_from_config(args.config), apply=args.apply,
        ))
    except (AppSkillDataError, FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"{'Applied' if args.apply else 'Preview'}: {skills} App Skill owners, {configs} integration configurations.")


if __name__ == "__main__":
    main()
