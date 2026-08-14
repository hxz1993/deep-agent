from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.planning import InMemoryPlanStore, Planning

from deep_agent.agent.deps import AgentDeps

PLANNING_TOOL_NAMES = (
    "write_plan",
    "read_plan",
    "add_task",
    "update_task_status",
    "update_task_statuses",
    "remove_task",
)
PLANNING_TOOL_NAME_SET = frozenset(PLANNING_TOOL_NAMES)
PLAN_METADATA_KEY = "deepagent_plan"


def is_planning_tool(tool_name: str | None) -> bool:
    """Return whether ``tool_name`` belongs to Harness Planning."""

    return bool(tool_name and tool_name in PLANNING_TOOL_NAME_SET)


def prepare_plan_store(deps: AgentDeps) -> InMemoryPlanStore:
    """Reset the official in-memory plan at the start of a DeepAgent Run."""

    store = InMemoryPlanStore()
    deps.plan_store = store
    return store


def _resolve_plan_store(ctx: RunContext[AgentDeps]) -> InMemoryPlanStore:
    """Resolve the current Run store for Harness without adding persistence."""

    store = ctx.deps.plan_store
    if store is None:
        store = prepare_plan_store(ctx.deps)
    return store


def build_planning_capability() -> Planning[AgentDeps]:
    """Build the default Harness 0.18 Planning capability for one Agent."""

    return Planning(
        store_resolver=_resolve_plan_store,
        enable_subtasks=False,
        inject=True,
    )


async def current_plan_snapshot(deps: AgentDeps) -> dict[str, Any]:
    """Read a stable, JSON-safe UI snapshot from the official PlanStore."""

    store = deps.plan_store
    items = [] if store is None else await store.get_items()
    serialized = [item.model_dump(mode="json") for item in items]
    return {
        "items": serialized,
        "total": len(serialized),
        "completed": sum(item.get("status") == "completed" for item in serialized),
    }


def attach_plan_snapshot(part: ToolReturnPart, snapshot: Mapping[str, Any]) -> None:
    """Attach a product presentation snapshot to a normal Pydantic Tool Return."""

    metadata = dict(part.metadata) if isinstance(part.metadata, Mapping) else {}
    metadata[PLAN_METADATA_KEY] = dict(snapshot)
    part.metadata = metadata


def plan_snapshot_from_part(part: ToolReturnPart) -> dict[str, Any] | None:
    """Read the internal plan snapshot without exposing unrelated part metadata."""

    if not isinstance(part.metadata, Mapping):
        return None
    snapshot = part.metadata.get(PLAN_METADATA_KEY)
    return dict(snapshot) if isinstance(snapshot, Mapping) else None


def attach_known_plan_snapshots(
    messages: Sequence[ModelMessage],
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    final_snapshot: Mapping[str, Any] | None = None,
) -> None:
    """Ensure accepted planning returns carry snapshots before persistence.

    Streaming captures every accepted mutation by call id. Non-streaming runs do
    not expose intermediate events, so their final snapshot is attached to the
    last accepted Planning return.
    """

    planning_returns: list[ToolReturnPart] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or not is_planning_tool(part.tool_name):
                continue
            planning_returns.append(part)
            snapshot = snapshots.get(part.tool_call_id)
            if snapshot is not None:
                attach_plan_snapshot(part, snapshot)
    if planning_returns and final_snapshot is not None:
        last = planning_returns[-1]
        if plan_snapshot_from_part(last) is None:
            attach_plan_snapshot(last, final_snapshot)
