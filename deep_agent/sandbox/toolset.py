from __future__ import annotations

import uuid

from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.registry import ToolInfo, ToolPackage
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.profile import ResolvedSandboxProfile, format_profile_tool_instructions
from deep_agent.sandbox.types import ExecuteRequest, JsonValue

__all__ = ["build_python_sandbox_tool_package"]

_FALLBACK_INSTRUCTIONS = (
    "每次调用 execute_python 都会启动全新的 Python 执行环境，不保留变量、"
    "函数或 import。需要继续计算时，必须再次传入完整代码和 inputs。"
    "结构化返回值必须写在代码最后一行作为表达式，pandas DataFrame/Series 同样以表达式结尾，"
    "沙箱会把预览写入 result。"
)


def build_python_sandbox_tool_package(
    backend: PythonExecutionBackend,
    *,
    timeout_sec: float,
    sandbox_profile: ResolvedSandboxProfile | None = None,
) -> ToolPackage:
    timeout_ms = int(timeout_sec * 1000)

    async def execute_python(
        ctx: RunContext[AgentDeps],
        code: str,
        inputs: dict[str, JsonValue] | None = None,
    ) -> dict[str, object]:
        """Run one stateless Python data-computation request.

        The last line of ``code`` must be an expression (not ``print``) so its
        value is serialized into the ``result`` field. ``print`` only fills
        ``stdout`` and leaves ``result`` null.
        """
        if ctx.deps.user is None:
            raise ModelRetry("python-sandbox 需要已认证用户，无法在无用户身份时执行")

        request = ExecuteRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            inputs=inputs or {},
            timeout_ms=timeout_ms,
        )
        result = await backend.execute(request)
        return result.model_dump(mode="json")

    instructions = (
        format_profile_tool_instructions(sandbox_profile)
        if sandbox_profile is not None
        else _FALLBACK_INSTRUCTIONS
    )

    toolset = FunctionToolset(
        instructions=instructions,
        tools=[execute_python],
    )

    return ToolPackage(
        code="builtin:sandbox",
        category="builtin",
        toolset=toolset,
        label="Python 沙箱",
        source="builtin",
        description="在无状态 Python 环境中执行数据计算代码。",
        tools=[
            ToolInfo(
                "execute_python",
                "执行 Python",
                "执行一次性 Python 代码；最后一行表达式进入 result，print 仅进入 stdout。",
            ),
        ],
        default_enabled=False,
    )
