"""Unify product tool packages and group manual UI tools.

Revision ID: 002
Revises: 001
Create Date: 2026-08-04
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import boolean_default, key_type, now_default, text_json_type


revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEEPCUBE_PACKAGE = "ui:deepcube"
DEEPCUBE_TOOLS = {"deep_cube__clarify", "deep_cube__config_fill"}
BUILTIN_UI_REFERENCES = {
    "ui:builtin__confirm": "ui:builtin",
    "ui:builtin__choice": "ui:builtin",
}


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _json_type():
    return text_json_type(_bind())


def _now_default():
    return now_default(_bind())


def _true_default():
    return boolean_default(_bind(), value=True)


def _as_capabilities(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"agents.capabilities contains an invalid value: {value!r}")
    return value


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _upgrade_capabilities(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value == "native":
            result.append("builtin:general")
        elif value == "python-sandbox":
            result.append("builtin:sandbox")
        elif value == "attachment":
            result.append("builtin:attachment")
        elif value in BUILTIN_UI_REFERENCES:
            result.append(BUILTIN_UI_REFERENCES[value])
        elif value.startswith("ui:"):
            tool_name = value.removeprefix("ui:")
            if tool_name not in DEEPCUBE_TOOLS:
                raise RuntimeError(
                    f"Agent references an unknown UI tool that cannot be migrated: {value}"
                )
            result.append(DEEPCUBE_PACKAGE)
        else:
            result.append(value)
    return _deduplicate(result)


def _downgrade_capabilities(
    values: list[str], package_members: dict[str, list[str]]
) -> list[str]:
    result: list[str] = []
    for value in values:
        if value == "builtin:general":
            result.append("native")
        elif value == "builtin:sandbox":
            result.append("python-sandbox")
        elif value == "builtin:attachment":
            result.append("attachment")
        elif value == "ui:builtin":
            result.extend(["ui:builtin__confirm", "ui:builtin__choice"])
        elif value.startswith("ui:"):
            if value not in package_members:
                raise RuntimeError(f"UI package cannot be expanded during downgrade: {value}")
            result.extend(f"ui:{name}" for name in package_members[value])
        else:
            result.append(value)
    return _deduplicate(result)


def _rewrite_agents(transform) -> None:
    bind = _bind()
    agents = sa.table(
        "agents",
        sa.column("id", sa.String(36)),
        sa.column("capabilities", _json_type()),
    )
    rows = bind.execute(sa.select(agents.c.id, agents.c.capabilities)).mappings().all()
    for row in rows:
        capabilities = _as_capabilities(row["capabilities"])
        if capabilities is None:
            continue
        updated = transform(capabilities)
        bind.execute(
            agents.update()
            .where(agents.c.id == row["id"])
            .values(capabilities=json.dumps(updated, ensure_ascii=False, separators=(",", ":")))
        )


def _validate_upgrade_data() -> set[str]:
    """Reject unmappable rows before any DDL, including non-transactional MySQL DDL."""
    bind = _bind()
    tools = sa.table(
        "ui_tool_definitions",
        sa.column("name", _key_type()),
        sa.column("source", sa.String(16)),
    )
    rows = bind.execute(sa.select(tools.c.name, tools.c.source)).all()
    builtin_rows = sorted(str(name) for name, source in rows if source == "builtin")
    if builtin_rows:
        raise RuntimeError(
            "Builtin UI tools must not be stored in ui_tool_definitions: "
            + ", ".join(builtin_rows)
        )
    manual_names = {str(name) for name, _source in rows}
    unknown_names = manual_names - DEEPCUBE_TOOLS
    if unknown_names:
        raise RuntimeError(
            "Manual UI tools must be assigned explicitly before migration: "
            + ", ".join(sorted(unknown_names))
        )

    agents = sa.table(
        "agents",
        sa.column("id", sa.String(36)),
        sa.column("capabilities", _json_type()),
    )
    for row in bind.execute(sa.select(agents.c.id, agents.c.capabilities)).mappings():
        capabilities = _as_capabilities(row["capabilities"])
        if capabilities is not None:
            for value in capabilities:
                if value in BUILTIN_UI_REFERENCES or not value.startswith("ui:"):
                    continue
                tool_name = value.removeprefix("ui:")
                if tool_name in DEEPCUBE_TOOLS and tool_name not in manual_names:
                    raise RuntimeError(
                        "Agent references a UI tool definition that does not exist: "
                        f"{value}"
                    )
            _upgrade_capabilities(capabilities)
    return manual_names


def upgrade() -> None:
    manual_names = _validate_upgrade_data()

    op.create_table(
        "ui_tool_packages",
        sa.Column("code", _key_type(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=_true_default(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False
        ),
        sa.PrimaryKeyConstraint("code"),
    )

    with op.batch_alter_table("ui_tool_definitions") as batch:
        batch.add_column(sa.Column("package_code", _key_type(), nullable=True))

    bind = _bind()
    packages = sa.table(
        "ui_tool_packages",
        sa.column("code", _key_type()),
        sa.column("label", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("source", sa.String(16)),
        sa.column("enabled", sa.Boolean()),
    )
    tools = sa.table(
        "ui_tool_definitions",
        sa.column("name", _key_type()),
        sa.column("package_code", _key_type()),
    )
    if manual_names:
        bind.execute(
            packages.insert().values(
                code=DEEPCUBE_PACKAGE,
                label="DeepCube 助手",
                description="DeepCube 助手专用澄清与配置填充交互。",
                source="manual",
                enabled=True,
            )
        )
        bind.execute(
            tools.update()
            .where(tools.c.name.in_(sorted(manual_names)))
            .values(package_code=DEEPCUBE_PACKAGE)
        )

    remaining = bind.execute(
        sa.select(tools.c.name).where(tools.c.package_code.is_(None))
    ).scalars().all()
    if remaining:
        raise RuntimeError(
            "UI tools remain without a package after migration: "
            + ", ".join(sorted(map(str, remaining)))
        )

    _rewrite_agents(_upgrade_capabilities)

    with op.batch_alter_table("ui_tool_definitions") as batch:
        batch.alter_column("package_code", existing_type=_key_type(), nullable=False)
        batch.create_foreign_key(
            "ui_tool_definitions_package_code_fkey",
            "ui_tool_packages",
            ["package_code"],
            ["code"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_ui_tool_definitions_package_code", ["package_code"])


def downgrade() -> None:
    bind = _bind()
    tools = sa.table(
        "ui_tool_definitions",
        sa.column("name", _key_type()),
        sa.column("package_code", _key_type()),
    )
    package_members: dict[str, list[str]] = {}
    for row in bind.execute(
        sa.select(tools.c.package_code, tools.c.name).order_by(
            tools.c.package_code, tools.c.name
        )
    ).all():
        package_members.setdefault(str(row[0]), []).append(str(row[1]))

    _rewrite_agents(lambda values: _downgrade_capabilities(values, package_members))

    with op.batch_alter_table("ui_tool_definitions") as batch:
        batch.drop_index("ix_ui_tool_definitions_package_code")
        batch.drop_constraint("ui_tool_definitions_package_code_fkey", type_="foreignkey")
        batch.drop_column("package_code")
    op.drop_table("ui_tool_packages")
