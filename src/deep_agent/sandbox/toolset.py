from __future__ import annotations

import uuid

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolReturn

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.registry import ToolInfo, ToolPackage
from deep_agent.files.upload_settings import load_upload_settings_for_tool
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.file_bridge import SandboxFileInput, prepare_input_files, publish_execution_result
from deep_agent.sandbox.profile import (
    ResolvedSandboxProfile,
    format_profile_tool_instructions,
    format_python_tool_instructions,
)
from deep_agent.sandbox.types import ExecuteRequest, JsonValue

__all__ = ["build_python_sandbox_tool_package"]

_FALLBACK_INSTRUCTIONS = format_python_tool_instructions(
    (
        "numpy",
        "pandas",
        "pydantic",
        "requests",
        "httpx",
        "Pillow",
        "matplotlib",
        "pypdf",
        "openpyxl",
        "python-docx",
        "python-pptx",
    )
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
        files: list[SandboxFileInput] | None = None,
    ) -> ToolReturn:
        """使用第三方库进行计算、文件解析或转换，可读取会话文件并发布产物。

        已关联 PTC 时，内存中的字符串／JSON提取、列表过滤去重和简单汇总直接在
        run_code 中完成，不必为这些处理启动本工具。网页检索与正文抓取使用已提供的
        web_search／web_fetch，勿仅为提取网页链接而用 Python 重做同一请求。
        需要本工具的库或文件能力时，可把它作为 PTC 中的一步，直接传入前一工具的
        JSON 变量，再将本工具结果交给下一工具，无需模型转抄中间数据。

        公网文件无需修改、只需原样传给 MCP 时，不要用本工具重复抓取或探测网络；
        应在 PTC 中使用 load_file_data(url=...) 直接完成读取、上传和发送依赖链。
        只有确实需要解析、转码或生成新文件时才使用 Python 沙箱。
        用户仅要求发送/转交 WebP 等公网文件时不属于转码需求，保持原文件中转即可；
        只有用户明确要求 PNG/JPEG 或内联图片时才尝试转换。
        """
        if ctx.deps.user is None:
            raise ModelRetry("python-sandbox 需要已认证用户，无法在无用户身份时执行")

        upload_settings = await load_upload_settings_for_tool(ctx)
        sandbox_files = await prepare_input_files(
            ctx, files, upload_settings, tool_name="execute_python",
        )

        request = ExecuteRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            inputs=inputs or {},
            timeout_ms=timeout_ms,
            files=sandbox_files,
        )
        result = await backend.execute(request)
        return await publish_execution_result(
            ctx, result, upload_settings, tool_name="execute_python", input_files=sandbox_files,
        )

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
        description="在无状态 Python 环境中执行数据计算。",
        tools=[
            ToolInfo(
                "execute_python",
                "执行 Python",
                "执行无状态 Python 计算；可读取当前会话文件、访问公网并发布转换产物到 FileStore。",
            ),
        ],
    )
