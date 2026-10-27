from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from pydantic_ai import CancellationToken

from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.files.upload_settings import DefaultUploadSettingsStore

if TYPE_CHECKING:
    from deep_agent.agent.request_budget import TurnRequestBudget
    from deep_agent.agent.context.runtime import ContextRunState
    from deep_agent.agent.tool_discovery import ToolDiscoveryTelemetry
    from deep_agent.agent.tool_output_limits import ToolOutputTelemetry
    from pydantic_ai_harness.planning import InMemoryPlanStore
    from deep_agent.files.storage import FileStore
    from deep_agent.documents.cache import DocumentRunCache


@dataclass
class AgentDeps:
    """Per-run dependencies injected into tools via ``RunContext[AgentDeps]``.

    Add request-scoped services here (db handles, http clients, the current
    user, feature flags). Tools declare ``ctx: RunContext[AgentDeps]`` to read them.

    ``request_headers`` carries a whitelist of HTTP headers from the inbound client
    request. MCP toolsets read them when opening HTTP connections or when calling
    MCP tools (via connection headers and MCP ``_meta``).
    """

    session_id: str
    user: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    resume_from_run_id: str | None = None
    app_id: str | None = None
    space_id: str | None = None
    request_headers: Mapping[str, str] = field(default_factory=dict)
    file_store: "FileStore | None" = field(default=None, repr=False)
    upload_settings_store: Any = field(default_factory=DefaultUploadSettingsStore, repr=False)
    context_state: "ContextRunState | None" = None
    request_budget: "TurnRequestBudget | None" = None
    output_state: Any = field(default=None, repr=False)
    output_store: Any = field(default=None, repr=False)
    tool_discovery: "ToolDiscoveryTelemetry | None" = None
    tool_output_limits: "ToolOutputTelemetry | None" = None
    # Names only, per Run, for model-wire diagnostics; never a tool authority.
    model_api_previous_tools: dict[str, set[str]] = field(default_factory=dict, repr=False)
    plan_store: "InMemoryPlanStore | None" = field(default=None, repr=False)
    plan_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    tool_discovery_inputs_explicit: bool = False
    tool_discovery_primary_query: str = ""
    tool_discovery_supplemental_query: str = ""
    skill_runtime: dict[str, Any] | None = None
    loaded_skill_codes: list[str] = field(default_factory=list)
    cancellation_token: CancellationToken | None = field(default=None, repr=False)
    has_context_references: bool = False
    attachment_by_id: dict[str, AttachmentRef] = field(default_factory=dict)
    # All FileStore-backed artifacts authorized by the raw current-session history.
    # Overflow artifacts are also kept in ``tool_output_artifacts`` because only
    # those handles may be consumed by Harness ``read_tool_result``.
    session_file_artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_output_artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    file_bytes: dict[str, bytes] = field(default_factory=dict)
    file_parsed: dict[str, Any] = field(default_factory=dict)
    file_load_tasks: dict[str, asyncio.Task[bytes]] = field(
        default_factory=dict,
        repr=False,
    )
    file_parse_tasks: dict[str, asyncio.Task[Any]] = field(
        default_factory=dict,
        repr=False,
    )
    document_cache: "DocumentRunCache | None" = field(default=None, repr=False)
    code_mode_file_payloads: dict[str, Any] = field(default_factory=dict, repr=False)
    ptc_diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    ptc_generated_files: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)
