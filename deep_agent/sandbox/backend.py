from __future__ import annotations

from typing import Protocol, runtime_checkable

from deep_agent.sandbox.types import BackendHealth, ExecuteRequest, ExecuteResult

__all__ = ["PythonExecutionBackend"]


@runtime_checkable
class PythonExecutionBackend(Protocol):
    async def health(self) -> BackendHealth: ...

    async def execute(self, request: ExecuteRequest) -> ExecuteResult: ...

    async def aclose(self) -> None: ...
