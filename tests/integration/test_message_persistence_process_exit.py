"""Hard-process-death recovery on disposable SQLite, without network Providers."""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import text

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.conversation import project_session_events
from tests.helpers.request_identity import TEST_USER_ID
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


async def _run_waiting_fixture(url: str, agent_id: str, session: str, connection) -> None:
    stores, engine, _ = await make_sqlite_stores(url)
    persister = EventPersister(stores.coordinator)
    registry = ActiveRunRegistry()
    await persister.start()
    slow_started = asyncio.Event()
    requests = 0

    async def fast_read() -> str:
        # A durable isolated counter proves recovery never re-executes a tool.
        async with engine.begin() as db:
            await db.execute(text("INSERT INTO fixture_executions (tool) VALUES ('fast_read')"))
        return "FAST_RESULT_SURVIVES_KILL"

    async def slow_read() -> str:
        slow_started.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def stream(messages, info):
        nonlocal requests
        requests += 1
        if requests != 1:
            raise AssertionError("the barrier must prevent another model request")
        yield "COMPLETED_ROUND_SURVIVES_KILL"
        yield {
            0: DeltaToolCall(name="fast_read", json_args="{}", tool_call_id="fast-before-kill"),
            1: DeltaToolCall(name="slow_read", json_args="{}", tool_call_id="slow-at-kill"),
        }

    runner = AgentRunner(
        Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
              toolsets=[FunctionToolset(tools=[fast_read, slow_read])]),
        stores.coordinator, UsageLimits(request_limit=4), active_runs=registry,
    )
    try:
        with patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            async for event in runner.run_stream(
                session, "synthetic process recovery", AgentDeps(
                    session_id=session, agent_id=agent_id, user=TEST_USER_ID,
                ), persister=persister,
            ):
                if event["type"] == "tool_result" and event.get("tool_call_id") == "fast-before-kill":
                    await asyncio.wait_for(slow_started.wait(), timeout=5)
                    connection.send({"state": "completed_fact_boundary", "pid": os.getpid()})
        connection.send({"state": "unexpected_terminal"})
    finally:
        registry.cancel_all()
        await persister.aclose()
        await engine.dispose()


def _fixture_process(url: str, agent_id: str, session: str, connection) -> None:
    """Spawn entrypoint owns only synthetic services and its disposable database."""
    try:
        asyncio.run(_run_waiting_fixture(url, agent_id, session, connection))
    except BaseException as exc:
        connection.send({"state": "error", "type": type(exc).__name__})
        raise
    finally:
        connection.close()


class MessagePersistenceProcessExitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-message-process-exit-", dir=Path(__file__).resolve().parents[2],
        )
        self.url = f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'recovery.sqlite3'}"
        self.stores, self.engine, _ = await make_sqlite_stores(self.url)
        self.agent_id = await seed_default_agent(self.stores, name="isolated-process-recovery")
        async with self.engine.begin() as db:
            await db.execute(text("CREATE TABLE fixture_executions (tool TEXT NOT NULL)"))
        self.processes = []
        self.connections = []

    async def asyncTearDown(self) -> None:
        for process in self.processes:
            if process.is_alive():
                process.kill()
            await asyncio.to_thread(process.join, 5)
            self.assertFalse(process.is_alive(), "owned fixture process leaked")
            process.close()
        for connection in self.connections:
            connection.close()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def _reopen(self) -> None:
        await self.engine.dispose()
        self.stores, self.engine, _ = await make_sqlite_stores(self.url)

    async def _history(self, session: str):
        stored = await self.stores.coordinator.list_all_events(session)
        return stored, project_session_events(stored, session_id=session)

    async def _execution_count(self) -> int:
        async with self.engine.connect() as db:
            return int((await db.execute(text("SELECT count(*) FROM fixture_executions"))).scalar_one())

    @unittest.skipUnless(os.name == "posix", "this scenario verifies actual POSIX SIGKILL")
    async def test_sigkill_preserves_committed_facts_without_reexecuting_tools(self):
        session = "sigkill-durable-facts"
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        self.connections.append(receive)
        process = context.Process(target=_fixture_process, args=(self.url, self.agent_id, session, send))
        process.start()
        self.processes.append(process)
        send.close()
        self.assertTrue(await asyncio.to_thread(receive.poll, 20), "fixture did not reach its completed_fact_boundary barrier")
        message = receive.recv()
        self.assertEqual(message, {"state": "completed_fact_boundary", "pid": process.pid})
        self.assertTrue(process.is_alive())
        before, visible = await self._history(session)
        self.assertFalse(any(event.event_type.startswith("run/") for event in before))
        tools = {item.call_id: item for item in visible.items if item.kind == "tool"}
        self.assertEqual(tools["fast-before-kill"].output, "FAST_RESULT_SURVIVES_KILL")
        self.assertEqual(tools["fast-before-kill"].status, "completed")
        self.assertIsNone(tools["slow-at-kill"].output)
        self.assertEqual(await self._execution_count(), 1)

        # No shutdown hook can run: only the previously committed completed_fact_boundary survives.
        process.kill()
        await asyncio.to_thread(process.join, 5)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        await self._reopen()
        recovered, history = await self._history(session)
        self.assertEqual(len([item for item in history.items if item.kind == "user_message"]), 1)
        self.assertEqual(
            [item.text for item in history.items if item.kind == "assistant_message" and item.text],
            ["COMPLETED_ROUND_SURVIVES_KILL"],
        )
        tools = {item.call_id: item for item in history.items if item.kind == "tool"}
        self.assertEqual(set(tools), {"fast-before-kill", "slow-at-kill"})
        self.assertEqual((tools["fast-before-kill"].status, tools["fast-before-kill"].output),
                         ("completed", "FAST_RESULT_SURVIVES_KILL"))
        self.assertIsNone(tools["slow-at-kill"].output)
        # No cross-process recovery/lease mechanism is added. The complete facts
        # survive unchanged; a forced kill cannot invent a tool result or error.
        self.assertEqual([event.public_dict() for event in recovered],
                         [event.public_dict() for event in before])
        self.assertFalse(any(event.event_type == "execution/error" for event in recovered))
        self.assertEqual(await self._execution_count(), 1)

        committed = [event.public_dict() for event in recovered]
        after, second_history = await self._history(session)
        self.assertEqual([event.public_dict() for event in after], committed)
        self.assertEqual(second_history.model_dump(), history.model_dump())
        self.assertEqual(await self._execution_count(), 1)

    async def test_restart_leaves_canonical_pending_deferred_actionable(self):
        session = "restart-legitimate-pending"

        async def stream(messages, info):
            yield "AWAITING_USER_CONFIRMATION"
            yield {0: DeltaToolCall(
                name="builtin__confirm", json_args='{"message":"Synthetic restart confirmation"}',
                tool_call_id="durable-confirm",
            )}

        persister = EventPersister(self.stores.coordinator)
        registry = ActiveRunRegistry()
        runner = AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                  toolsets=[build_external_toolset(builtin_ui_records())]),
            self.stores.coordinator, UsageLimits(request_limit=4), active_runs=registry,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}},
        )
        await persister.start()
        try:
            with patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
                events = [event async for event in runner.run_stream(
                    session, "synthetic deferred restart", AgentDeps(
                        session_id=session, agent_id=self.agent_id, user=TEST_USER_ID,
                    ), persister=persister,
                )]
            self.assertEqual(events[-1]["status"], "pending")
        finally:
            registry.cancel_all()
            await persister.aclose()

        before, before_history = await self._history(session)
        await self._reopen()
        after, history = await self._history(session)
        self.assertEqual([event.public_dict() for event in after], [event.public_dict() for event in before])
        self.assertEqual(history.model_dump(), before_history.model_dump())
        tools = [item for item in history.items if item.kind == "tool"]
        self.assertEqual([(item.call_id, item.status) for item in tools], [("durable-confirm", "awaiting_input")])
        self.assertEqual(await self._execution_count(), 0)
