"""JSON contracts for administrator-only, read-only diagnostic queries."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deep_agent.schemas.session_source import SessionSourceType
from deep_agent.time import UtcDatetime


class DiagnosticQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosticUsageQuery(DiagnosticQueryBody):
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    timezone: str = Field(default="UTC", max_length=64)
    agent_id: str | None = Field(default=None, max_length=256)
    source_type: SessionSourceType | None = None
    source_id: str | None = Field(default=None, min_length=1, max_length=255)


class DiagnosticSessionQuery(DiagnosticUsageQuery):
    user_id: str | None = Field(default=None, max_length=128)
    app_id: str | None = Field(default=None, max_length=256)
    query: str | None = Field(default=None, max_length=256)
    has_questions: bool | None = None
    has_execution_error: bool | None = None
    latest_status: Literal["running", "waiting", "completed", "stopped", "error", "unknown", "not_started"] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class DiagnosticUsersQuery(DiagnosticSessionQuery):
    after: str | None = Field(default=None, max_length=128)


class DiagnosticUserProfileQuery(DiagnosticQueryBody):
    user_id: str = Field(min_length=1, max_length=128)


class DiagnosticSessionDetailQuery(DiagnosticQueryBody):
    include_context: bool = False


class DiagnosticSessionEventsQuery(DiagnosticQueryBody):
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)
    event_type: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    call_id: str | None = Field(default=None, max_length=256)


class DiagnosticChannelPeersQuery(DiagnosticSessionQuery):
    kind: Literal["user", "group"] = "user"
    options_only: bool = False
    include_group_senders: bool = False
    sender_id: str | None = Field(default=None, min_length=1, max_length=256)
    peer_id: str | None = Field(default=None, min_length=1, max_length=256)


class DiagnosticChannelSessionsQuery(DiagnosticSessionQuery):
    peer_id: str = Field(min_length=1, max_length=256)
    sender_id: str | None = Field(default=None, min_length=1, max_length=256)
    kind: Literal["user", "group"] = "user"


class DiagnosticUsageDetailsQuery(DiagnosticUsageQuery):
    group_by: Literal["model", "user", "agent"] = "model"
    sort: Literal["total_tokens", "input_tokens", "output_tokens", "requests"] = "total_tokens"
    descending: bool = True
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class DiagnosticUsageSessionsQuery(DiagnosticUsageQuery):
    group_by: Literal["model", "user", "agent"]
    key: str = Field(max_length=256)
    offset: int = Field(default=0, ge=0)


class DiagnosticUsageExportQuery(DiagnosticUsageQuery):
    group_by: Literal["model", "user", "agent"] = "model"
    sort: Literal["total_tokens", "input_tokens", "output_tokens", "requests"] = "total_tokens"
    descending: bool = True
    scope: Literal["all", "page"] = "all"
    offset: int = Field(default=0, ge=0)


class DiagnosticLogsQuery(DiagnosticQueryBody):
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    source: Literal["api", "gateway"] | None = None
    level: str | None = Field(default=None, max_length=16)
    session_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    q: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=200, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=64)
