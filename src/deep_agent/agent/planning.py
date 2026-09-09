from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.planning import InMemoryPlanStore, PlanItem, Planning

from deep_agent.agent.deps import AgentDeps

PLANNING_TOOL_NAMES = ("write_plan",)
PLANNING_TOOL_NAME_SET = frozenset(PLANNING_TOOL_NAMES)
PLANNING_GUIDANCE = """Use planning only when the task benefits from multiple execution steps.

- Include only work that still needs to be performed. Never add "write the final answer", "reply to the user", or equivalent presentation-only steps.
- Every write_plan call replaces the complete plan. Include the current status of every remaining step in each call.
- Update all accurately completed work before starting the final answer. Once final-answer text begins, do not call write_plan again.
- A deferred-tool resume belongs to the same user turn and receives the last complete plan snapshot from that turn.
- Never mark unfinished work as completed merely to close the plan.
"""
PLAN_METADATA_KEY = "deepagent_plan"


def is_planning_tool(tool_name: str | None) -> bool:
    """Return whether ``tool_name`` belongs to Harness Planning."""

    return bool(tool_name and tool_name in PLANNING_TOOL_NAME_SET)


def prepare_plan_store(deps: AgentDeps) -> InMemoryPlanStore:
    """Create a fresh official PlanStore for the current Pydantic Run."""

    store = InMemoryPlanStore()
    deps.plan_store = store
    return store


async def restore_plan_snapshot(
    deps: AgentDeps,
    snapshot: Mapping[str, Any] | None,
) -> InMemoryPlanStore:
    """Restore a complete write_plan snapshot for a Deferred run in the same turn."""

    store = prepare_plan_store(deps)
    if not snapshot:
        return store
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return store
    items = [PlanItem.model_validate(item) for item in raw_items if isinstance(item, Mapping)]
    await store.set_items(items)
    return store


def _resolve_plan_store(ctx: RunContext[AgentDeps]) -> InMemoryPlanStore:
    """Resolve the current Run store for Harness without adding persistence."""

    store = ctx.deps.plan_store
    if store is None:
        store = prepare_plan_store(ctx.deps)
    return store


def build_planning_capability() -> Planning[AgentDeps]:
    """Build the scoped Harness Planning capability for one Agent."""

    return Planning(
        tools=PLANNING_TOOL_NAMES,
        guidance=PLANNING_GUIDANCE,
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
            if plan_snapshot_from_part(part) is not None:
                # Provider call IDs may repeat in a later response. The
                # snapshot on this completed result is already authoritative.
                continue
            snapshot = snapshots.get(part.tool_call_id)
            if snapshot is not None:
                attach_plan_snapshot(part, snapshot)
    if planning_returns and final_snapshot is not None:
        last = planning_returns[-1]
        if plan_snapshot_from_part(last) is None:
            attach_plan_snapshot(last, final_snapshot)
