"""Current defaults for a brand-new database, separate from released revisions.

Only the Alembic environment's empty-database-to-head path calls this function.
Existing installations, including unchanged legacy defaults, must stay untouched.
"""

from __future__ import annotations

import json
import sqlalchemy as sa
from sqlalchemy.engine import Connection


def initialize_current_defaults(connection: Connection) -> None:
    # Released 001 seeds native search. Only fresh databases pass through here;
    # leave existing installations untouched and do not seed a search provider.
    global_settings = sa.table("global_settings", sa.column("key", sa.String()))
    connection.execute(global_settings.delete().where(global_settings.c.key == "web_search"))

    # Preserve the baseline IDs and Agent association; no credentials are seeded.
    providers = sa.table(
        "llm_providers",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("provider_type", sa.String()),
        sa.column("use_default_base_url", sa.Boolean()),
        sa.column("base_url", sa.Text()),
    )
    models = sa.table(
        "llm_models",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("model", sa.Text()),
        sa.column("context_window_tokens", sa.BigInteger()),
        sa.column("max_output_tokens", sa.BigInteger()),
        sa.column("input_modalities", sa.Text()),
        sa.column("tags", sa.Text()),
    )
    connection.execute(
        providers.update()
        .where(providers.c.id == "00000000000000000000000000000001")
        .values(
            name="DeepSeek",
            provider_type="deepseek",
            use_default_base_url=True,
            base_url="https://api.deepseek.com",
        )
    )
    connection.execute(
        models.update()
        .where(models.c.id == "00000000-0000-0000-0000-000000000001")
        .values(
            name="deepseek-flash",
            model="deepseek-flash",
            context_window_tokens=1_000_000,
            max_output_tokens=384_000,
            input_modalities=json.dumps(["text", "image"]),
            tags=json.dumps([
                "attachment", "reasoning", "tool_call", "structured_output",
                "temperature", "open_weights", "interleaved",
            ]),
        )
    )
