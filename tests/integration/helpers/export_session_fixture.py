"""Export a persisted chat session from DB to a JSON fixture for integration tests.

Usage (from repository root):

    venv\\Scripts\\python.exe -m tests.integration.helpers.export_session_fixture \\
        --session-id <uuid> \\
        --agent-id <agent-uuid> \\
        --output tests/fixtures/sessions/deepcube_tiered_rebate_full.json

Requires ``DEEP_AGENT_DBMS_*`` or default SQLite (``.env`` / ``DEEP_AGENT_SQLITE_PATH``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from deep_agent.db.factory import create_engine as create_db_engine
from deep_agent.db.models import ChatMessage, ChatSession
from deep_agent.db.session_scope import agent_scope_to_api, normalize_agent_scope
from deep_agent.db.url import get_database_url

DEFAULT_FULL_DEEPCUBE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "sessions" / "deepcube_tiered_rebate_full.json"
)


async def export_session_fixture(
    session_id: str,
    *,
    agent_id: str | None = None,
    output: str | Path,
) -> Path:
    """Write ``{session_id, agent_id, title, messages[]}`` JSON for ``seed_session_from_fixture``."""
    database_url = get_database_url()
    engine, session_factory = create_db_engine(database_url)
    scope = normalize_agent_scope(agent_id)
    api_agent_id = agent_id or agent_scope_to_api(scope)

    try:
        async with session_factory() as db:
            title_row = (
                await db.execute(
                    select(ChatSession.title).where(
                        ChatSession.agent_id == scope,
                        ChatSession.session_id == session_id,
                    )
                )
            ).one_or_none()
            rows = (
                await db.execute(
                    select(ChatMessage.sequence, ChatMessage.payload)
                    .where(
                        ChatMessage.agent_id == scope,
                        ChatMessage.session_id == session_id,
                    )
                    .order_by(ChatMessage.sequence)
                )
            ).all()
    finally:
        await engine.dispose()

    if not rows:
        raise ValueError(f"No messages for session_id={session_id!r}, agent_id={api_agent_id!r}")

    fixture = {
        "session_id": session_id,
        "agent_id": api_agent_id,
        "title": title_row[0] if title_row is not None else None,
        "messages": [{"sequence": int(seq), "payload": payload} for seq, payload in rows],
    }

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export chat session DB rows to a test fixture JSON file.")
    parser.add_argument("--session-id", required=True, help="chat_sessions.session_id")
    parser.add_argument(
        "--agent-id",
        default="",
        help="Agent UUID for scoped sessions; omit or empty for anonymous sessions",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_FULL_DEEPCUBE_FIXTURE,
        help=f"Output path (default: {DEFAULT_FULL_DEEPCUBE_FIXTURE.name})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    agent_id = args.agent_id.strip() or None
    out = asyncio.run(
        export_session_fixture(
            args.session_id,
            agent_id=agent_id,
            output=args.output,
        )
    )
    fixture = json.loads(out.read_text(encoding="utf-8"))
    sequences = [item["sequence"] for item in fixture["messages"]]
    print(f"Wrote {len(sequences)} messages -> {out}")
    print(f"sequences: {sequences}")


if __name__ == "__main__":
    main()
