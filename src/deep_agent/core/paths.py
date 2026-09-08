from __future__ import annotations

from pathlib import Path

# Resolve the single physical src layout. Runtime data and external-profile
# checks stay repo-relative; no root-level compatibility links are required.
SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent
DEEP_AGENT_ROOT = REPO_ROOT

CONSOLE_ROOT = REPO_ROOT / "console"
MCP_SERVERS_ROOT = REPO_ROOT / "mcp_servers"
