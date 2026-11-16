"""Independent A2A SDK server used for outbound interoperability acceptance.

Run from the repository root with ``python tests/integration/fixtures/remote_a2a_server.py``.
This application imports the upstream server, with no DeepAgent handler or stores.
"""
from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass

import uvicorn
from a2a import types as wire
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from starlette.applications import Starlette


@dataclass
class Budget:
    rows: list[dict[str, str]]
    month: str | None = None
    region: str | None = None
    difference: int | None = None


def _choose(values: set[str], text: str, aliases: dict[str, tuple[str, ...]]) -> str | None:
    text = text.casefold()
    matches = [value for value in values if any(
        match.casefold() in text for match in (value, *aliases.get(value.casefold(), ()))
    )]
    return matches[0] if len(matches) == 1 else None


class BudgetExecutor(AgentExecutor):
    def __init__(self, *, streaming: bool) -> None:
        self.budgets: dict[str, Budget] = {}
        self.streaming = streaming

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id, message = context.task_id, context.context_id, context.message
        assert task_id and context_id and message
        initial = context.current_task is None
        if initial:
            await event_queue.enqueue_event(wire.Task(id=task_id, context_id=context_id, status=wire.TaskStatus(
                state=wire.TaskState.TASK_STATE_SUBMITTED,
            )))
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()
        config = context.configuration
        if context.tenant != "budget-fixture" or (not self.streaming and not (config and config.return_immediately)):
            await updater.failed(updater.new_agent_message([wire.Part(text="缺少租户或异步启动参数。")]))
            return
        budget = self.budgets.get(context_id)
        if budget is None:
            try:
                attachment = next(part for part in message.parts if part.filename.endswith(".csv") and part.HasField("raw"))
                rows = list(csv.DictReader(io.StringIO(attachment.raw.decode("utf-8-sig"))))
                if not rows or not {"month", "region", "budget", "actual"} <= rows[0].keys():
                    raise ValueError
            except (StopIteration, ValueError, csv.Error):
                await updater.failed(updater.new_agent_message([
                    wire.Part(text="请提供包含 month、region、budget、actual 的 UTF-8 CSV。"),
                ]))
                return
            self.budgets[context_id] = Budget(rows)
            await updater.requires_input(updater.new_agent_message([wire.Part(text="请确认月份。")]))
            return

        if budget.difference is not None:
            await updater.complete(updater.new_agent_message([
                wire.Part(text=f"沿用 {budget.month} / {budget.region}，差额 {budget.difference}。"),
            ]))
            return

        text = context.get_user_input()
        if budget.month is None:
            months = {row["month"] for row in budget.rows}
            budget.month = _choose(months, text, {"november": ("十一月", "11月"), "october": ("十月", "10月")})
            question = "请确认月份。" if budget.month is None else "请确认地区，并附上确认文件。"
            await updater.requires_input(updater.new_agent_message([wire.Part(text=question)]))
            return

        rows = [row for row in budget.rows if row["month"] == budget.month]
        region = _choose({row["region"] for row in rows}, text, {"east": ("华东",), "west": ("华西",)})
        if not region or not any(part.HasField("raw") and part.raw for part in message.parts):
            await updater.requires_input(updater.new_agent_message([wire.Part(text="请确认地区，并附上确认文件。")]))
            return
        row = next(row for row in rows if row["region"] == region)
        try:
            difference = int(row["actual"]) - int(row["budget"])
        except (ValueError, TypeError):
            await updater.failed(updater.new_agent_message([wire.Part(text="预算或实际值无效。")]))
            return
        budget.region, budget.difference = region, difference
        result = io.StringIO(newline="")
        writer = csv.writer(result, lineterminator="\n")
        writer.writerow(["month", "region", "difference", "nonce"])
        writer.writerow([budget.month, region, difference, row.get("nonce", "")])
        await updater.add_artifact([
            wire.Part(filename="result.csv", media_type="text/csv", raw=result.getvalue().encode()),
        ], name="result.csv", last_chunk=True)
        await updater.complete(updater.new_agent_message([wire.Part(text=f"预算差额 {difference}。")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def create_app(base_url: str, *, streaming: bool = True) -> Starlette:
    card = wire.AgentCard(
        name="A2A SDK Budget Fixture", description="Reconcile a CSV using its month and region.", version="1",
        supported_interfaces=[wire.AgentInterface(
            url=f"{base_url}/rpc", protocol_binding="JSONRPC", protocol_version="1.0", tenant="budget-fixture",
        )], capabilities=wire.AgentCapabilities(streaming=streaming),
        default_input_modes=["text/plain", "text/csv"], default_output_modes=["text/plain", "text/csv"],
        skills=[wire.AgentSkill(id="reconcile", name="Reconcile", description="CSV budget reconciliation", tags=["budget"])],
    )
    handler = DefaultRequestHandler(BudgetExecutor(streaming=streaming), InMemoryTaskStore(), card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/rpc")])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--polling", action="store_true")
    args = parser.parse_args()
    uvicorn.run(create_app(f"http://127.0.0.1:{args.port}", streaming=not args.polling),
                host="127.0.0.1", port=args.port, log_level="warning")
