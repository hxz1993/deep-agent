from __future__ import annotations

from pathlib import Path

# Resolve the physical source path, including when imported through a legacy
# root-level link. Runtime data and external-profile checks stay repo-relative.
SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent
DEEP_AGENT_ROOT = REPO_ROOT

CONSOLE_ROOT = REPO_ROOT / "console"
MCP_SERVERS_ROOT = REPO_ROOT / "mcp_servers"
