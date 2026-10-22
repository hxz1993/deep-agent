"""Optional plain text alongside an Agent's structured output contract.

Revision ID: 013
Revises: 012
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_output_configs") as batch:
        batch.add_column(sa.Column("allow_text_output", sa.Boolean(), nullable=False, server_default=sa.false()))


def _uses_text_output_policy(payload):
    if not isinstance(payload, dict):
        return False
    message = payload.get("message")
    metadata = message.get("metadata") if isinstance(message, dict) else None
    if not isinstance(metadata, dict):
        return False
    # Business JSON may legitimately use the same property name. Only these
    # platform-owned SDK metadata markers change how 012 would resume/display.
    return any(isinstance(metadata.get(key), dict)
               and metadata[key].get("allow_text_output") is True
               for key in ("deepagent_output_contract", "deepagent_output"))


def _preflight_remaining_downgrades(connection):
    """Fail before MySQL's implicit DDL commit if a later step would refuse.

    The environment uses one outer transaction. A later published protection
    cannot undo MySQL DROP COLUMN, while its rollback can undo alembic_version.
    Inspect the complete requested path before this migration emits any DDL.
    """
    context = op.get_context()
    script = context.opts.get("script")
    if script is None or "destination_rev" not in context.opts:
        raise RuntimeError("013 downgrade requires an explicit Alembic destination")
    path = {item.revision for item in script.iterate_revisions(
        context.get_current_heads(), context.opts["destination_rev"], select_for_downgrade=True)}
    if "011" in path:
        raise RuntimeError("011 cannot be downgraded automatically; refused before 013 DDL. Restore an approved pre-migration backup.")
    if "012" not in path:
        return
    # Mirror the immutable published 012 data-loss preconditions, without
    # invoking its downgrade function or modifying any historical revision.
    checks = {
        "output_configs": "SELECT COUNT(*) FROM agent_output_configs",
        "enabled_agents": "SELECT COUNT(*) FROM agents WHERE structured_output_enabled = :enabled",
        "updated_agents": "SELECT COUNT(*) FROM agents WHERE updated_by IS NOT NULL",
        "output_events": "SELECT COUNT(*) FROM chat_session_events WHERE event_type = 'output/attempt'",
        "output_snapshots": "SELECT COUNT(*) FROM chat_session_events WHERE payload LIKE '%deepagent_output_contract%'",
    }
    counts = {key: int(connection.scalar(sa.text(sql), {"enabled": True}) or 0) for key, sql in checks.items()}
    if any(counts.values()):
        raise RuntimeError("012 downgrade would discard structured-output or updater facts (preflight before 013 DDL): "
                           + ", ".join(f"{key}={value}" for key, value in counts.items())
                           + ". Keep 013 or restore an approved backup.")


def downgrade():
    connection = op.get_bind()
    configured = int(connection.scalar(sa.text(
        "SELECT COUNT(*) FROM agent_output_configs WHERE allow_text_output = :enabled"), {"enabled": True}) or 0)
    # An off Agent may still retain a valuable true draft. Frozen Turn contracts
    # also remain authoritative after the Agent flag has subsequently been reset.
    frozen = 0
    rows = connection.execute(sa.text(
        "SELECT payload FROM chat_session_events WHERE payload LIKE :marker"),
        {"marker": '%"allow_text_output"%'})
    for row in rows:
        try:
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("013 downgrade cannot verify an output contract; keep 013") from exc
        frozen += int(_uses_text_output_policy(payload))
    if configured or frozen:
        raise RuntimeError(
            "013 downgrade would discard text-output policy or frozen contracts: "
            f"configured_output_configs={configured}, frozen_events={frozen}. Keep 013 or restore an approved backup.")
    _preflight_remaining_downgrades(connection)
    with op.batch_alter_table("agent_output_configs") as batch:
        batch.drop_column("allow_text_output")
