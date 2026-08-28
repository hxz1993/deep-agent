from __future__ import annotations

import base64
import asyncio
import unittest
from dataclasses import replace

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities import build_registry
from deep_agent.code_mode import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.code_mode.toolset import HybridCodeModeToolset, _scrub_error_text
from deep_agent.code_mode.file_payload import (
    FILE_DATA_TOKEN_PREFIX,
    MAX_FILE_BYTES,
    FilePayload,
    _resolve_payload_value,
    build_code_mode_tool_package,
    safe_audit_value,
)
from deep_agent.files.storage import StoredFile
from deep_agent.schemas.attachment import AttachmentRef


class _MemoryFileStore:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloads = 0

    async def download(self, file_id, request_headers):
        del request_headers
        self.downloads += 1
        return self.files[file_id]

    async def upload(self, **kwargs):
        return StoredFile(
            id="uploaded",
            file_name=kwargs["file_name"],
            file_size=len(kwargs["data"]),
            file_type=kwargs["file_type"],
            mime=kwargs.get("mime"),
        )

    async def delete(self, file_id, request_headers):
        del file_id, request_headers

    async def register_download(self, **kwargs):
        del kwargs
        return "download-record"


class CodeModeTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_exposes_explicit_default_off_package(self) -> None:
        package = build_registry().get("builtin:code-mode")
        self.assertEqual(package.label, "代码编排")
        self.assertEqual([tool.name for tool in package.tools], ["run_code"])

    def test_small_file_payload_is_removed_from_error_text(self) -> None:
        encoded = base64.b64encode(b"small-secret").decode("ascii")
        scrubbed = _scrub_error_text(
            f"upstream rejected {encoded}",
            sensitive_values=frozenset({encoded}),
        )
        self.assertNotIn(encoded, scrubbed)
        self.assertIn("[file-payload-redacted]", scrubbed)
        identity_scrubbed = _scrub_error_text(
            "<extra_identity_context>private identity</extra_identity_context>",
            sensitive_values=frozenset(),
        )
        self.assertNotIn("private identity", identity_scrubbed)

    async def test_hybrid_tool_is_native_and_callable_from_run_code(self) -> None:
        seen_tools: list[list[str]] = []
        called: list[tuple[int, int]] = []

        async def add(a: int, b: int) -> dict[str, int]:
            called.append((a, b))
            return {"sum": a + b}

        steps = 0

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal steps
            steps += 1
            seen_tools.append([tool.name for tool in info.function_tools])
            if steps == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_code",
                            {"code": "value = await demo_add(a=2, b=3)\nvalue"},
                            "code-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(
                    FunctionToolset(tools=[add]).prefixed("demo")
                ),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        result = await agent.run("add", deps=AgentDeps(session_id="session"))

        self.assertEqual(result.output, "done")
        self.assertEqual(called, [(2, 3)])
        self.assertIn("run_code", seen_tools[0])
        self.assertIn("demo_add", seen_tools[0])
        self.assertNotIn("load_file_data", seen_tools[0])
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(returns[0].content, {"sum": 5})
        self.assertEqual(returns[0].metadata["code_mode"], True)
        self.assertEqual(returns[0].metadata["nested_calls"][0]["tool_name"], "demo_add")

    async def test_native_schema_is_unchanged_and_code_repl_state_is_run_scoped(self) -> None:
        schemas: list[tuple[str, dict, str | None]] = []
        step = 0

        async def lookup(query: str, limit: int = 3) -> dict[str, object]:
            """Look up a bounded number of records."""
            return {"query": query, "limit": limit}

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal step
            step += 1
            native = next(tool for tool in info.function_tools if tool.name == "demo_lookup")
            schemas.append((native.name, native.parameters_json_schema, native.description))
            if step == 1:
                return ModelResponse(
                    parts=[ToolCallPart("run_code", {"code": "value = 41\nvalue"}, "state-1")]
                )
            if step == 2:
                return ModelResponse(
                    parts=[ToolCallPart("run_code", {"code": "value + 1"}, "state-2")]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(
                    FunctionToolset(tools=[lookup]).prefixed("demo")
                ),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        await agent.run("state", deps=AgentDeps(session_id="session-1"))

        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[0][0], "demo_lookup")
        self.assertIn("query", schemas[0][1]["properties"])
        self.assertEqual(schemas[0][2], "Look up a bounded number of records.")

        second_run_step = 0

        def second_run(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal second_run_step
            second_run_step += 1
            if second_run_step == 1:
                return ModelResponse(
                    parts=[ToolCallPart("run_code", {"code": "value"}, "isolated")]
                )
            return ModelResponse(parts=[TextPart(content="isolated")])

        isolated_agent = Agent(
            FunctionModel(second_run),
            deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True, max_retries=1)],
        )
        result = await isolated_agent.run("state", deps=AgentDeps(session_id="session-2"))
        self.assertEqual(result.output, "isolated")

    async def test_asyncio_gather_can_fan_out_and_aggregate(self) -> None:
        started = 0
        both_started = asyncio.Event()

        async def fetch(value: int) -> dict[str, int]:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return {"value": value}

        step = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal step
            step += 1
            if step == 1:
                code = (
                    "import asyncio\n"
                    "rows = await asyncio.gather(demo_fetch(value=2), demo_fetch(value=1))\n"
                    "sorted([row['value'] for row in rows])"
                )
                return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "fanout")])
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(FunctionToolset(tools=[fetch]).prefixed("demo")),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        result = await agent.run("fanout", deps=AgentDeps(session_id="session"))
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(returns[0].content, [1, 2])
        self.assertEqual(started, 2)

    async def test_file_payload_is_resolved_only_at_mcp_boundary_and_redacted(self) -> None:
        raw = b"\x89PNG\r\n\x1a\nDeepAgent"
        encoded = base64.b64encode(raw).decode("ascii")
        received: dict[str, str] = {}

        async def upload_media(file_name: str, base64_content: str) -> dict[str, str]:
            received.update(file_name=file_name, base64_content=base64_content)
            return {"media_id": "media-1"}

        steps = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal steps
            steps += 1
            if steps == 1:
                code = (
                    "payload = await load_file_data(file_id='file-1', encoding='base64')\n"
                    "uploaded = await wecom_upload_media("
                    "file_name=payload['name'], base64_content=payload['content'])\n"
                    "uploaded"
                )
                return ModelResponse(
                    parts=[ToolCallPart("run_code", {"code": code}, "code-file")]
                )
            return ModelResponse(parts=[TextPart(content="sent")])

        store = _MemoryFileStore({"file-1": raw})
        deps = AgentDeps(
            session_id="session",
            user="user",
            file_store=store,
            attachment_by_id={
                "file-1": AttachmentRef(
                    id="file-1",
                    name="image.png",
                    mime="image/png",
                )
            },
        )
        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(
                    FunctionToolset(tools=[upload_media]).prefixed("wecom")
                ),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        result = await agent.run("send image", deps=deps)

        self.assertEqual(received, {"file_name": "image.png", "base64_content": encoded})
        self.assertEqual(store.downloads, 1)
        serialized = str(result.all_messages())
        self.assertNotIn(encoded, serialized)
        self.assertNotIn("deepagent-file-data://", serialized)
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        nested = returns[0].metadata["nested_calls"]
        self.assertEqual(
            [item["tool_name"] for item in nested],
            ["load_file_data", "wecom_upload_media"],
        )
        self.assertEqual(
            nested[1]["args"]["base64_content"],
            "[file-payload-redacted]",
        )
        self.assertEqual(nested[1]["result"], {"media_id": "media-1"})

    async def test_text_file_is_strictly_decoded_without_creating_payload_token(self) -> None:
        raw = "预算：100".encode("gb18030")
        step = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal step
            step += 1
            if step == 1:
                code = (
                    "payload = await load_file_data(file_id='text-1', encoding='text', "
                    "text_encoding='gb18030', max_chars=16)\n"
                    "payload['content']"
                )
                return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "text")])
            return ModelResponse(parts=[TextPart(content="done")])

        deps = AgentDeps(
            session_id="session",
            file_store=_MemoryFileStore({"text-1": raw}),
            attachment_by_id={
                "text-1": AttachmentRef(id="text-1", name="预算.txt", mime="text/plain")
            },
        )
        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        result = await agent.run("read text", deps=deps)
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(returns[0].content, "预算：100")
        self.assertFalse(deps.code_mode_file_payloads)

    def test_payload_resolution_requires_an_exact_run_scoped_token(self) -> None:
        token = f"{FILE_DATA_TOKEN_PREFIX}{'a' * 32}"
        payload = FilePayload(
            token=token,
            file_id="file-1",
            name="tiny.bin",
            mime="application/octet-stream",
            byte_size=3,
            sha256="unused",
            data=b"abc",
        )
        deps = AgentDeps(
            session_id="session",
            code_mode_file_payloads={token: payload},
        )

        self.assertEqual(_resolve_payload_value(token, deps), "YWJj")
        self.assertEqual(
            _resolve_payload_value(f"prefix:{token}:suffix", deps),
            f"prefix:{token}:suffix",
        )
        with self.assertRaisesRegex(Exception, "无效、已过期"):
            _resolve_payload_value(f"{FILE_DATA_TOKEN_PREFIX}{'b' * 32}", deps)

    def test_small_payload_base64_is_redacted_by_exact_run_secret(self) -> None:
        deps = AgentDeps(session_id="session")
        token = f"{FILE_DATA_TOKEN_PREFIX}{'c' * 32}"
        payload = FilePayload(
            token=token,
            file_id="file-1",
            name="tiny.bin",
            mime="application/octet-stream",
            byte_size=3,
            sha256="unused",
            data=b"abc",
        )
        deps.code_mode_file_payloads[token] = payload
        encoded = base64.b64encode(payload.data).decode("ascii")

        self.assertEqual(
            safe_audit_value(
                {"content": encoded},
                sensitive_values=frozenset({token, encoded}),
            ),
            {"content": "[file-payload-redacted]"},
        )

    def test_identity_context_is_removed_from_nested_tool_results(self) -> None:
        projected = safe_audit_value(
            {
                "extra_identity_context": "private operator identity",
                "sessions_count": 2,
            }
        )
        self.assertEqual(
            projected,
            {
                "extra_identity_context": "[redacted]",
                "sessions_count": 2,
            },
        )
        projected_output = safe_audit_value(
            {
                "output": "debug: {'extra_identity_context': "
                "'<extra_identity_context>private identity</extra_identity_context>'}",
            }
        )
        self.assertNotIn("private identity", str(projected_output))
        self.assertIn("[identity-context-redacted]", str(projected_output))
        credential_output = safe_audit_value(
            {"output": "Authorization: Bearer abcdefghijklmnop api_key=secret-value"}
        )
        self.assertNotIn("abcdefghijklmnop", str(credential_output))
        self.assertNotIn("secret-value", str(credential_output))

    async def test_catalog_teaches_top_level_await_without_asyncio_run(self) -> None:
        async def lookup(query: str) -> dict[str, str]:
            return {"query": query}

        descriptions: list[str] = []

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            descriptions.append(
                next(tool.description or "" for tool in info.function_tools if tool.name == "run_code")
            )
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(
                    FunctionToolset(tools=[lookup]).prefixed("demo")
                ),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=False)],
        )
        await agent.run("inspect", deps=AgentDeps(session_id="session"))

        self.assertIn("never wrap it with `asyncio.run()`", descriptions[0])
        self.assertIn("one tool's result feeds another", descriptions[0])
        self.assertIn("execute the\n  remaining chain in one `run_code` call", descriptions[0])
        catalog = HybridCodeModeToolset._render_catalog(
            {
                "load_file_data": type("Definition", (), {"metadata": {}})(),
                "demo_lookup": type("Definition", (), {"metadata": {}})(),
            }
        )
        self.assertIn("await demo_lookup(...)", catalog)
        self.assertIn("await load_file_data", catalog)

    async def test_unknown_file_id_fails_closed(self) -> None:
        calls = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_code",
                            {"code": "await load_file_data(file_id='missing')"},
                            "code-missing",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="closed")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True, max_retries=1)],
        )
        result = await agent.run("missing", deps=AgentDeps(session_id="session"))
        self.assertEqual(result.output, "closed")

    async def test_declared_oversized_artifact_fails_before_download(self) -> None:
        calls = 0
        store = _MemoryFileStore({"artifact-1": b"must-not-download"})

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_code",
                            {"code": "await load_file_data(file_id='artifact-1')"},
                            "oversized",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="closed")])

        deps = AgentDeps(
            session_id="session",
            file_store=store,
            tool_output_artifacts={
                "artifact-1": {
                    "artifact_id": "artifact-1",
                    "byte_size": MAX_FILE_BYTES + 1,
                    "file_name": "large.json",
                }
            },
        )
        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True, max_retries=1)],
        )
        result = await agent.run("large", deps=deps)
        self.assertEqual(result.output, "closed")
        self.assertEqual(store.downloads, 0)

    async def test_alias_collision_keeps_both_tools_native_only(self) -> None:
        toolset = FunctionToolset[AgentDeps]()

        @toolset.tool_plain(name="a-b")
        def first(value: int) -> int:
            return value

        @toolset.tool_plain(name="a.b")
        def second(value: int) -> int:
            return value

        seen: dict[str, str] = {}

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            seen.update({tool.name: tool.description or "" for tool in info.function_tools})
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(toolset),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        await agent.run("inspect", deps=AgentDeps(session_id="session"))
        self.assertIn("a-b", seen)
        self.assertIn("a.b", seen)
        self.assertIn("run_code", seen)

    async def test_code_only_helper_never_falls_back_native_on_alias_collision(self) -> None:
        helpers = FunctionToolset[AgentDeps]()

        @helpers.tool_plain(
            name="hidden-helper",
            metadata={"deepagent_code_mode": "code_only"},
        )
        def hidden_helper(value: int) -> int:
            return value

        hybrid = FunctionToolset[AgentDeps]()

        @hybrid.tool_plain(name="hidden.helper")
        def native_peer(value: int) -> int:
            return value

        seen: set[str] = set()

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            seen.update(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[helpers, FilePayloadResolvingToolset(hybrid)],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        await agent.run("inspect", deps=AgentDeps(session_id="session"))

        self.assertNotIn("hidden-helper", seen)
        self.assertIn("hidden.helper", seen)
        self.assertIn("run_code", seen)

    async def test_hybrid_does_not_reveal_deferred_tool_natively(self) -> None:
        async def hidden(value: int) -> int:
            return value

        base = FunctionToolset(tools=[hidden])
        original_get_tools = base.get_tools

        async def deferred_get_tools(ctx):
            tools = await original_get_tools(ctx)
            return {
                name: replace(
                    tool,
                    tool_def=replace(tool.tool_def, defer_loading=True),
                )
                for name, tool in tools.items()
            }

        base.get_tools = deferred_get_tools  # type: ignore[method-assign]
        seen: set[str] = set()

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            seen.update(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(base),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        await agent.run("inspect", deps=AgentDeps(session_id="session"))
        self.assertEqual(seen, {"run_code"})

    async def test_deferred_tool_becomes_native_and_code_callable_after_reveal(self) -> None:
        revealed = False
        called: list[str] = []
        base = FunctionToolset[AgentDeps]()

        @base.tool_plain
        def reveal_tools() -> dict[str, object]:
            nonlocal revealed
            revealed = True
            return {"discovered_tools": [{"name": "hidden_lookup"}]}

        @base.tool_plain
        def hidden_lookup(query: str) -> dict[str, str]:
            called.append(query)
            return {"query": query}

        original_get_tools = base.get_tools

        async def dynamic_get_tools(ctx):
            tools = await original_get_tools(ctx)
            output = {}
            for name, tool in tools.items():
                if name == "reveal_tools":
                    output[name] = replace(
                        tool,
                        tool_def=replace(tool.tool_def, tool_kind="capability-load"),
                    )
                elif name == "hidden_lookup":
                    output[name] = replace(
                        tool,
                        tool_def=replace(tool.tool_def, defer_loading=not revealed),
                    )
                else:
                    output[name] = tool
            return output

        base.get_tools = dynamic_get_tools  # type: ignore[method-assign]
        seen_steps: list[set[str]] = []
        step = 0

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal step
            step += 1
            seen_steps.append({tool.name for tool in info.function_tools})
            if step == 1:
                return ModelResponse(parts=[ToolCallPart("reveal_tools", {}, "search")])
            if step == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_code",
                            {"code": "await hidden_lookup(query='revealed')"},
                            "code-revealed",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(base),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        await agent.run("reveal", deps=AgentDeps(session_id="session"))

        self.assertEqual(seen_steps[0], {"reveal_tools", "run_code"})
        self.assertEqual(seen_steps[1], {"reveal_tools", "hidden_lookup", "run_code"})
        self.assertEqual(called, ["revealed"])


if __name__ == "__main__":
    unittest.main()
