"""deep-agent: a general-purpose AI agent backend built on FastAPI + Pydantic AI."""

from __future__ import annotations

import sys

from deep_agent.core.paths import DEEP_AGENT_ROOT

if str(DEEP_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEEP_AGENT_ROOT))

__version__ = "0.4.2"
