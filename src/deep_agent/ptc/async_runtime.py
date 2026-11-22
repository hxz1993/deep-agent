"""Async Monty transport for the pinned Harness CodeMode tool lifecycle.

Keep the SDK's tool manager, type stubs, call budget and execution records. Only
worker transport is adapted: its synchronous feed/resume driver otherwise blocks
the API event loop and prevents an asyncio deadline from interrupting CPU work.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import anyio
from pydantic_ai import ToolDenied
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, UserError
from pydantic_ai.messages import ToolCallPart, ToolReturn, ToolReturnPart
from pydantic_ai.tool_manager import ToolManager
from pydantic_monty import (
    AsyncFunctionSnapshot, AsyncMonty, MontyComplete, MontyCrashedError,
    MontyRuntimeError, MontySyntaxError, MontyTypingError,
)
from pydantic_ai_harness._monty_exec import is_sandbox_panic

# Compatibility surface for the explicitly pinned pydantic-ai-harness version.
# Reuse its conversion and generated signatures rather than redefining either.
from pydantic_ai_harness.code_mode._toolset import (
    _TOOL_RETURN_CONTENT_TA,
    _is_duration_exhausted,
    _jsonable_for_sandbox,
    _resolve_resource_limits,
    global_mode_is_sequential,
)
from deep_agent.ptc.limits import PTC_EXECUTION_SECONDS
from deep_agent.ptc.diagnostics import current_collector


@dataclass
class _Runtime:
    pool_stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    session_stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    pool: Any = None
    session: Any = None
    active: set[asyncio.Task[Any]] = field(default_factory=set)
    thread_calls: set[Future[Any]] = field(default_factory=set)
    callback_lock: Any = field(default_factory=threading.Lock)
    # Source bodies already live in the outer tool-call events. Keep only the
    # feed -> event association here, scoped to this REPL, including failed feeds.
    sources: dict[str, tuple[str, int]] = field(default_factory=dict)
    next_feed: int = 0

    async def cancel_callbacks(self) -> None:
        # Monty cancels its worker I/O, but does not join coroutine externals.
        # Join them before releasing their session or enclosing MCP connections.
        pending = list(self.active)
        for task in pending:
            task.cancel()
        with self.callback_lock:
            thread_calls = list(self.thread_calls)
        for future in thread_calls:
            future.cancel()
        if pending:
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*pending, return_exceptions=True)

    async def reset(self) -> None:
        await self.cancel_callbacks()
        with anyio.CancelScope(shield=True):
            await self.session_stack.aclose()
        self.session_stack = AsyncExitStack()
        self.session = None
        self.sources.clear()
        self.next_feed = 0

    async def close(self) -> None:
        await self.reset()
        with anyio.CancelScope(shield=True):
            await self.pool_stack.aclose()
        self.pool_stack = AsyncExitStack()
        self.pool = None


def _runtime(toolset: Any) -> _Runtime:
    state = toolset._run_state
    if state is None:
        raise UserError("CodeModeToolset must be entered before executing code")
    # All views created by CodeMode during one Run share this SDK state object.
    runtime = getattr(state, "_deepagent_async_runtime", None)
    if runtime is None:
        runtime = _Runtime()
        setattr(state, "_deepagent_async_runtime", runtime)
    return runtime


async def reset_runtime(toolset: Any) -> None:
    state = toolset._run_state
    if state is not None:
        runtime = getattr(state, "_deepagent_async_runtime", None)
        if runtime is not None:
            await runtime.reset()
        state.has_executed_feed = False


async def close_runtime(toolset: Any) -> None:
    state = toolset._run_state
    if state is not None:
        runtime = getattr(state, "_deepagent_async_runtime", None)
        if runtime is not None:
            await runtime.close()
            delattr(state, "_deepagent_async_runtime")


async def execute_code(toolset: Any, code: str, ctx: Any, tool: Any, execution: Any) -> Any:
    """Execute a CodeMode snippet through official nonblocking Monty APIs."""
    state = toolset._run_state
    runtime = _runtime(toolset)
    parent = ctx.tool_manager
    if parent is None:
        raise UserError("CodeMode requires the Agent tool manager")
    manager = ToolManager(
        toolset=toolset.wrapped, root_capability=parent.root_capability,
        ctx=ctx, tools=tool.wrapped_tools,
    )
    loop = asyncio.get_running_loop()
    parent_context = contextvars.copy_context()
    serial = asyncio.Lock() if global_mode_is_sequential(manager.get_parallel_execution_mode) else None
    completed: list[asyncio.Event] = []
    host_cancellation: asyncio.CancelledError | None = None
    collector = current_collector.get()
    source_location: dict[str, Any] | None = None

    async def invoke(alias: str, call_id: str, args: dict[str, Any], done: asyncio.Event,
                     predecessors: list[asyncio.Event], location: dict[str, Any] | None) -> Any:
        nonlocal host_cancellation
        task = asyncio.current_task()
        assert task is not None
        runtime.active.add(task)
        original = tool.sanitized_to_original.get(alias, alias)
        call = ToolCallPart(tool_name=original, args=args, tool_call_id=call_id)
        execution.nested_calls[call_id] = call
        if collector is not None and location is not None:
            collector.locate(call_id, original, location)
        try:
            for predecessor in predecessors:
                await predecessor.wait()
            if serial is not None:
                async with serial:
                    result = await manager.handle_call(call, wrap_validation_errors=False)
            else:
                result = await manager.handle_call(call, wrap_validation_errors=False)
            if isinstance(result, ToolDenied):
                execution.nested_returns[call_id] = ToolReturnPart(
                    original, result.message, call_id, outcome="denied",
                )
                raise RuntimeError(f"Tool {original!r} call denied: {result.message}")
            metadata = result.metadata if isinstance(result, ToolReturn) else None
            value = result.return_value if isinstance(result, ToolReturn) else result
            execution.nested_returns[call_id] = ToolReturnPart(
                original, value, call_id, metadata=metadata,
            )
            return _jsonable_for_sandbox(_TOOL_RETURN_CONTENT_TA.dump_python(value, warnings=False))
        except (CallDeferred, ApprovalRequired) as exc:
            execution.nested_returns[call_id] = ToolReturnPart(
                original, {"error_type": type(exc).__name__}, call_id, outcome="failed",
            )
            raise UserError(
                f"Tool {original!r} raised {type(exc).__name__} inside code mode; "
                "deferred and approval-required calls must use the native tool path."
            ) from exc
        except asyncio.CancelledError as exc:
            if task.cancelling():
                # Monty transports a cancelled coroutine as MontyRuntimeError,
                # while the worker's parent task may not yet be cancelling.
                # Retain the actual host Task cancellation, not error text.
                host_cancellation = exc
            execution.nested_returns.setdefault(call_id, ToolReturnPart(
                original, "Tool execution interrupted", call_id, outcome="interrupted",
            ))
            raise
        except Exception as exc:
            execution.nested_returns.setdefault(call_id, ToolReturnPart(
                original, {"error_type": type(exc).__name__}, call_id, outcome="failed",
            ))
            raise
        finally:
            done.set()
            runtime.active.discard(task)

    def dispatch(alias: str, **kwargs: Any) -> Any:
        # Monty invokes this synchronous callback in its I/O thread. Reserve
        # before returning a coroutine: a large gather cannot spawn unbounded
        # host tasks before the 100-call budget is checked.
        with runtime.callback_lock:
            call_id = execution.next_tool_call_id(max_tool_calls=toolset.max_tool_calls)
            done = asyncio.Event()
            definition = tool.callable_defs[alias]
            predecessors = list(completed) if definition.sequential else []
            completed.append(done)
            location = source_location
        coroutine = invoke(alias, call_id, kwargs, done, predecessors, location)
        if not definition.sequential:
            return coroutine
        # A sequential tool has a synchronous stub. Marshal its async execution
        # to the main loop while blocking only Monty's callback thread; preserve
        # the PTC context so standard capability hooks still recognize the call.
        with runtime.callback_lock:
            future = parent_context.copy().run(asyncio.run_coroutine_threadsafe, coroutine, loop)
            runtime.thread_calls.add(future)
        try:
            return future.result()
        finally:
            with runtime.callback_lock:
                runtime.thread_calls.discard(future)

    fresh = not state.has_executed_feed
    try:
        if runtime.pool is None:
            runtime.pool = await runtime.pool_stack.enter_async_context(
                AsyncMonty(max_processes=1, request_timeout=PTC_EXECUTION_SECONDS),
            )
        if runtime.session is None:
            limits = _resolve_resource_limits(toolset.resource_limits)
            runtime.session = await runtime.session_stack.enter_async_context(runtime.pool.checkout(
                limits=limits,
                type_check=fresh and bool(tool.callable_defs),
                type_check_stubs=(toolset._build_type_check_stubs(tool.callable_defs)
                                  if fresh and tool.callable_defs else None),
            ))
        filename = f"<python-input-{runtime.next_feed}>"
        runtime.next_feed += 1
        runtime.sources[filename] = (ctx.tool_call_id or "", len(code.encode("utf-8")))
        snapshot = await runtime.session.feed_start(
            code,
            external_lookup={alias: partial(dispatch, alias) for alias in tool.callable_defs},
            print_callback=execution.capture.callback,
            os=toolset.os_access, mount=toolset.mount, skip_type_check=not fresh,
        )
        # Use Monty's own automatic resolution for every suspension, including
        # async futures and OS calls. Only inspect the call site; do not replace
        # tool dispatch, replay code or change timing/parallel-execution policy.
        while not isinstance(snapshot, MontyComplete):
            source_location = None
            try:
                if isinstance(snapshot, AsyncFunctionSnapshot):
                    position = snapshot.position
                    source = runtime.sources.get(position.filename)
                    if (source and source[0] and snapshot.function_name in tool.callable_defs
                            and snapshot.object_id is None
                            and 0 <= position.start < position.end <= source[1]):
                        source_location = {
                            "call_id": source[0], "filename": position.filename,
                            "start": position.start, "end": position.end,
                        }
            except Exception:
                # Optional diagnostics must never prevent an authorized call.
                if collector is not None:
                    collector.capture_error = True
            snapshot = await snapshot.resume_auto()
        result = snapshot.output
        state.has_executed_feed = True
        return _TOOL_RETURN_CONTENT_TA.validate_python(result) if result is not None else None
    except MontySyntaxError as exc:
        if fresh:
            await reset_runtime(toolset)
        raise ModelRetry(f"Syntax error in code:\n{execution.capture.prepend_to(exc.display())}") from exc
    except MontyTypingError as exc:
        await reset_runtime(toolset)
        raise ModelRetry(f"Type error in code:\n{execution.capture.prepend_to(exc.display())}") from exc
    except MontyRuntimeError as exc:
        if host_cancellation is not None:
            await reset_runtime(toolset)
            raise host_cancellation
        state.has_executed_feed = True
        message = f"Runtime error:\n{execution.capture.prepend_to(exc.display())}"
        if _is_duration_exhausted(exc) or exc.display(format="msg").startswith("memory limit exceeded:"):
            # Monty 1.5 warns that a resource trip can leave a half-mutated heap.
            # Discard it instead of feeding further code into uncertain state.
            await reset_runtime(toolset)
            message += "\n\nThe sandbox exceeded its execution or memory limit and its REPL state was reset."
        raise ModelRetry(message) from exc
    except MontyCrashedError as exc:
        await reset_runtime(toolset)
        raise ModelRetry("The sandbox worker stopped and its REPL state was reset.") from exc
    except asyncio.CancelledError:
        await reset_runtime(toolset)
        raise
    except Exception as exc:
        await reset_runtime(toolset)
        raise ModelRetry(f"Code execution failed and the session was reset: {type(exc).__name__}: {exc}") from exc
    except BaseException as exc:
        await reset_runtime(toolset)
        if is_sandbox_panic(exc):
            raise ModelRetry("The sandbox aborted and its REPL state was reset.") from exc
        raise
    finally:
        await runtime.cancel_callbacks()
