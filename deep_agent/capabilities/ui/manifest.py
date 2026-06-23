from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from deep_agent.capabilities.ui.constants import canonical_mcp_tool_name
from deep_agent.db.models import UiToolDefinition
from deep_agent.schemas.ui_tools import UiToolManifestEntry, UiToolsManifest


def manifest_entry_to_row(
    mcp_server_name: str,
    entry: Any,
    *,
    discovered_at: datetime,
) -> UiToolDefinition:
    if not isinstance(entry, UiToolManifestEntry):
        entry = UiToolManifestEntry.model_validate(entry)
    return UiToolDefinition(
        name=canonical_mcp_tool_name(mcp_server_name, entry.name),
        description=entry.description,
        parameters_json_schema=entry.parameters_json_schema,
        widget=entry.widget,
        result_schema=entry.result_schema,
        metadata_=entry.metadata,
        source="mcp",
        mcp_server_name=mcp_server_name,
        enabled=True,
        override=False,
        agent_ids=None,
        discovered_at=discovered_at,
    )


def parse_manifest_payload(raw: Any, *, mcp_server_name: str) -> list[UiToolDefinition]:
    if isinstance(raw, str):
        data = json.loads(raw) if raw.strip() else {}
    elif isinstance(raw, dict):
        data = raw
    else:
        data = json.loads(str(raw))
    manifest = UiToolsManifest.model_validate(data)
    if manifest.mcp_server and manifest.mcp_server != mcp_server_name:
        raise ValueError(
            f"manifest mcp_server {manifest.mcp_server!r} does not match {mcp_server_name!r}"
        )
    now = datetime.now().astimezone()
    return [manifest_entry_to_row(mcp_server_name, entry, discovered_at=now) for entry in manifest.tools]
