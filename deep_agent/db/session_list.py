from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MAX_LIST_LIMIT = 200

SortField = Literal["updated_at", "created_at"]


@dataclass(frozen=True)
class SessionListFilters:
    agent_id: str | None = None
    user_id: str | None = None
    limit: int = 50
    offset: int = 0
    updated_after: datetime | None = None
    updated_before: datetime | None = None
    has_messages: bool | None = None
    sort: SortField = "updated_at"


@dataclass(frozen=True)
class SessionSummaryRow:
    session_id: str
    agent_id: str | None
    agent_name: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    user_prompt: str | None = None
    title: str | None = None
