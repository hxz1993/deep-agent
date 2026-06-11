from __future__ import annotations

from pathlib import Path

# Project root (``mcps/``, ``skills/``, ``tests/``, ``.env``, etc.).
DEEP_AGENT_ROOT = Path(__file__).resolve().parents[2]

# Same as ``DEEP_AGENT_ROOT`` in the flattened layout (holds ``frontend/``).
REPO_ROOT = DEEP_AGENT_ROOT
