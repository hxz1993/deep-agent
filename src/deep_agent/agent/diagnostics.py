"""Pure observations of the SDK's existing requests and tool executions."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, ToolRetryError

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.observability import cache_usage_available, log_event
from deep_agent.agent.model_api_diagnostics import observed_model


class RuntimeDiagnostics(AbstractCapability[AgentDeps]):
    """No new schemas, messages, events, retries, network calls or run state."""

    def __init__(self, *, wait_after: float = 30, wait_interval: float = 60) -> None:
        self.wait_after = wait_after
        self.wait_interval = wait_interval

    @staticmethod
    def _fields(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        try:
            return {
                "session_id": ctx.deps.session_id, "agent_id": ctx.deps.agent_id,
                "conversation_id": ctx.deps.conversation_id or ctx.conversation_id,
                "run_id": ctx.deps.run_id or ctx.run_id,
                "request_index": ctx.run_step,
            }
        except Exception:
            return {}

    @staticmethod
    def _clock() -> float:
        try:
            return time.monotonic()
        except Exception:
            return 0.0

    def _duration(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))

    @staticmethod
    def _emit(event: str, **fields: Any) -> None:
        # The common writer is also failure-isolated. Guard here so instrumented
        # test/custom sinks cannot replace the original provider/tool outcome.
        try:
            log_event(event, **fields)
        except Exception:
            pass

    @asynccontextmanager
    async def _observe(self, prefix: str, fields: dict[str, Any]):
        started = self._clock()

        async def waiting() -> None:
            await asyncio.sleep(self.wait_after)
            while True:
                self._emit(prefix + "_still_waiting", **fields,
                           duration_ms=self._duration(started), status="running")
                await asyncio.sleep(self.wait_interval)

        self._emit(prefix + "_started", **fields)
        watchdog = None
        observation = waiting()
        try:
            watchdog = asyncio.create_task(observation, name="agent-diagnostic-wait")
        except Exception:
            observation.close()
        try:
            yield started
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                status = "cancelled"
            elif isinstance(exc, (ModelRetry, ToolRetryError)):
                status = "retrying"
            elif isinstance(exc, (CallDeferred, ApprovalRequired)):
                status = "deferred"
            else:
                status = "failed"
            self._emit(prefix + "_" + status, **fields, status=status,
                       duration_ms=self._duration(started),
                       http_status=getattr(exc, "status_code", None),
                       error_type=type(exc).__name__)
            raise
        finally:
            if watchdog is not None:
                try:
                    watchdog.cancel()
                    await asyncio.gather(watchdog, return_exceptions=True)
                except Exception:
                    pass

    async def wrap_model_request(self, ctx, *, request_context, handler):
        fields = self._fields(ctx)
        try:
            fields["model_name"] = ctx.model.model_name
        except Exception:
            pass
        async with self._observe("model_request", fields) as started:
            try:
                request_context.model = observed_model(
                    request_context.model, fields,
                    getattr(ctx.deps, "model_api_previous_tools", {}),
                )
            except Exception:
                pass
            response = await handler(request_context)
            extra = {}
            try:
                usage = response.usage
                cache_available = cache_usage_available([response])
                extra = dict(provider_name=response.provider_name, finish_reason=response.finish_reason,
                             input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                             cache_read_tokens=usage.cache_read_tokens if cache_available else None,
                             cache_write_tokens=usage.cache_write_tokens if cache_available else None)
            except Exception:
                pass
            self._emit("model_request_completed", **fields, **extra,
                       duration_ms=self._duration(started))
            return response

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        fields = self._fields(ctx)
        try:
            fields.update(tool=call.tool_name, tool_call_id=call.tool_call_id, attempt=ctx.retry + 1)
        except Exception:
            pass
        async with self._observe("agent_tool", fields) as started:
            result = await handler(args)
            self._emit("agent_tool_completed", **fields, status="completed",
                       duration_ms=self._duration(started))
            return result

    async def on_tool_validate_error(self, ctx, *, call, tool_def, args, error):
        try:
            self._emit("tool_validation_retry", **self._fields(ctx), tool=call.tool_name,
                       tool_call_id=call.tool_call_id, attempt=ctx.retry + 1,
                       error_type=type(error).__name__, phase="validation")
        except Exception:
            pass
        raise error
