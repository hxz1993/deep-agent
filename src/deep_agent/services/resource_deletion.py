"""Administrative deletion: confirm exact scope, stop execution, recheck in DB."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from deep_agent.db.deletion import DeletionConfirmationRequired


class DeleteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_token: str | None = None


async def delete_resource(ctx, store, resource_id: str, user_id: str, body, *, channel=False):
    token = body.confirmation_token if body else None
    try:
        impact = await store.deletion_impact(resource_id, user_id=user_id)
        if impact is None:
            raise HTTPException(status_code=404, detail="Channel account not found" if channel else "Agent not found")
        requires = (impact.counts["binding_count"] or impact.counts["run_count"]) if channel else impact.counts["session_count"]
        if (requires or token) and token != impact.token:
            raise DeletionConfirmationRequired(impact)
        scope = {"channel_account_id": resource_id} if channel else {"agent_id": resource_id}
        async with ctx.active_runs.deleting(**scope) as entries:
            pending_sessions = {(entry.agent_id, entry.session_id) for entry in entries}
            if not channel:
                pending_sessions.update((resource_id, sid) for sid in impact.session_ids)
            for agent_id, session_id in pending_sessions:
                await ctx.event_persister.flush(session_id, agent_id=agent_id, ignore_errors=True)
            delete = store.delete_account if channel else store.delete
            deleted = await delete(resource_id, user_id=user_id, confirmation_token=token)
            if not deleted:
                raise HTTPException(status_code=404, detail="Resource not found")
    except DeletionConfirmationRequired as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=409, detail="正在停止相关运行，尚未确认停止，本次未删除；请稍后重试") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
