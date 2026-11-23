"""Run execution context opt-in through the real SDK and Monty bridge."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.agent.factory import build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.options import OPTION
from deep_agent.security.current_user import CurrentUserProfileResolver
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.integration.helpers.chat import read_chat_stream
from tests.unit.test_execution_context_tools import (
    CONTEXT_AVATAR, CONTEXT_EMAIL, CONTEXT_NICKNAME, CONTEXT_USERNAME,
    RecordingContextBackend, context_deps, context_profile, decoded_context,
    execution_toolsets, python_call, skill_call, tool_returns,
)


class ExecutionContextRequestTests(unittest.IsolatedAsyncioTestCase):
    """HTTP identity/context -> real Runner/SDK/Monty, with MCP forwarding restricted."""

    async def asyncSetUp(self):
        from deep_agent.api.v1 import api_router
        from deep_agent.api.v1.chat import _agent_deps

        temporary = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-execution-context-request-", dir=Path(__file__).resolve().parents[2],
        )
        self.addCleanup(temporary.cleanup)
        stores, engine, session_factory = await make_sqlite_stores()
        self.backend = RecordingContextBackend()
        self.ctx = AppContext.for_testing(
            stores=stores, db_engine=engine, db_session_factory=session_factory,
            registry=build_registry(sandbox_backend=self.backend), sandbox_backend=self.backend,
            skill_runtime=SkillRuntimeLibrary(Path(temporary.name)),
        )
        init_context(self.ctx)
        self.app = FastAPI()
        self.app.include_router(api_router)
        install_test_user_header(self.app)
        self.app.state.test_agent_id = await seed_default_agent(stores)

        async def fetch(user, _headers):
            return context_profile(user)

        self.fetcher = AsyncMock(side_effect=fetch)
        self.resolver = CurrentUserProfileResolver(self.fetcher)
        resolver_patch = patch(
            "deep_agent.sandbox.context.get_current_user_profile_resolver", return_value=self.resolver,
        )
        self.get_resolver = resolver_patch.start()
        self.addCleanup(resolver_patch.stop)
        header_patch = patch.object(type(OPTION.agent), "mcp_forward_request_headers", frozenset({"x-tenant-id"}))
        header_patch.start()
        self.addCleanup(header_patch.stop)
        self.deps = []

        def capture_deps(ctx, **kwargs):
            deps = _agent_deps(ctx, **kwargs)
            self.deps.append(deps)
            return deps

        deps_patch = patch("deep_agent.api.v1.chat._agent_deps", side_effect=capture_deps)
        deps_patch.start()
        self.addCleanup(deps_patch.stop)

    async def asyncTearDown(self):
        try:
            await self.ctx.aclose()
        finally:
            reset_context()

    def _model(self, include_context):
        plan = [
            python_call(include_context=True if include_context else None),
            ("run_code", {"code": "value = await execute_python(code='42', "
                f"include_context={include_context!r})\nvalue['result']['answer']"}),
        ]

        def respond(messages, info):
            if not any(tool.name == "execute_python" for tool in info.function_tools):
                return ModelResponse(parts=[TextPart("done")])
            completed = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
            if completed < len(plan):
                name, args = plan[completed]
                return ModelResponse(parts=[ToolCallPart(name, args, f"request-{completed}")])
            return ModelResponse(parts=[TextPart("done")])

        async def stream(messages, info):
            for part in respond(messages, info).parts:
                if isinstance(part, ToolCallPart):
                    yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}
                elif isinstance(part, TextPart):
                    yield part.content

        return FunctionModel(function=respond, stream_function=stream)

    async def _request(self, headers, *, stream=False, include_context=True, session_id=None):
        body = {"agent_id": self.app.state.test_agent_id, "message": "Exercise request execution identity.",
                "capabilities": ["builtin:sandbox", "builtin:ptc"]}
        if session_id is not None:
            body["session_id"] = session_id
        with patch("deep_agent.agent.factory._resolve_model", return_value=self._model(include_context)):
            if stream:
                await read_chat_stream(self.app, json_body=body, headers=headers)
            else:
                async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                    response = await client.post("/api/v1/chat", json=body, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["output"], "done")

    async def test_chat_and_stream_capture_credentials_and_console_context_outside_mcp_allowlist(self):
        headers = {"user": "request-user", "authorization": "Bearer execution-fixture",
                   "cookie": "sid=execution-fixture", "token": "execution-token",
                   "x-user-token": "execution-user-token", "x-tenant-id": "execution-tenant",
                   "app": "platform001", "space": "platform", "language": "zh-cn",
                   "x-deep-agent-app": "execution007", "x-deep-agent-language": "en-us",
                   "x-private": "excluded"}
        for stream in (False, True):
            await self._request(headers, stream=stream)
            deps = self.deps[-1]
            self.assertEqual(deps.request_headers, {"user": "request-user", "x-tenant-id": "execution-tenant"})
            self.assertEqual((deps.app_id, deps.space_id), ("execution007", "execution"))
            self.assertNotIn("x-private", deps.execution_request_headers)
            self.assertNotIn("execution-fixture", repr(deps))
        self.assertEqual(len(self.backend.requests), 4)
        contexts = [decoded_context(request) for request in self.backend.requests]
        for value in contexts:
            self.assertEqual(value["user"]["id"], "request-user")
            self.assertEqual((value["app"], value["space"], value["language"]), ("execution007", "execution", "en-us"))
            self.assertNotIn("execution-fixture", json.dumps(value))
        self.fetcher.assert_awaited_once_with("request-user", {
            "authorization": "Bearer execution-fixture", "cookie": "sid=execution-fixture",
            "token": "execution-token", "x-user-token": "execution-user-token",
            "x-tenant-id": "execution-tenant", "user": "request-user", "language": "en-us",
        })

    async def test_default_false_never_queries_profile_in_chat_or_stream(self):
        for stream in (False, True):
            await self._request({"user": "request-user", "app": "execution007"},
                                stream=stream, include_context=False)
        self.assertEqual(len(self.backend.requests), 4)
        self.assertTrue(all(request.context_env == {} for request in self.backend.requests))
        self.get_resolver.assert_not_called()
        self.fetcher.assert_not_awaited()

    async def test_channel_owner_key_and_disabled_association_isolate_execution_context(self):
        from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate

        account = await self.ctx.stores.channel.create_account(ChannelAccountCreate(
            name="execution-context-channel", bot_id="execution-context-fixture", secret="fixture",
            agent_id=self.app.state.test_agent_id, session_user_id="channel-owner", enabled=True,
            settings={"deepfos": {"enabled": True, "app_id": "channel003", "api_key": "owned-key"}},
        ))
        binding = await self.ctx.stores.channel.resolve_binding(
            account_id=account.id, peer_type="single", peer_id="execution-context-fixture",
        )
        headers = {"user": "browser-owner", "authorization": "Bearer browser-fixture", "cookie": "sid=browser",
                   "x-deepfos-key": "forged-key", "app": "forged001", "space": "forged",
                   "x-deep-agent-app": "other001", "language": "en-us",
                   "x-deep-agent-channel-account": account.id, "x-deep-agent-channel-token": "internal-fixture"}
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "internal-fixture"}):
            await self._request(headers, session_id=binding.session.session_id)
            enabled = self.deps[-1].execution_request_headers
            self.assertEqual(enabled, {"user": "channel-owner", "x-deepfos-key": "owned-key",
                "app": "channel003", "space": "channel", "language": "en-us"})
            await self.ctx.stores.channel.update_account(account.id, ChannelAccountUpdate(settings={
                "deepfos": {"enabled": False, "app_id": "channel003", "api_key": "owned-key"},
            }))
            # A fresh channel binding is a separate user Run without old model history.
            disabled_binding = await self.ctx.stores.channel.resolve_binding(
                account_id=account.id, peer_type="single", peer_id="disabled-context-fixture",
            )
            await self._request(headers, stream=True, session_id=disabled_binding.session.session_id)
        self.assertEqual(self.deps[-1].execution_request_headers, {
            "user": "channel-owner", "authorization": "Bearer browser-fixture",
            "cookie": "sid=browser", "language": "en-us",
        })
        self.assertEqual(self.fetcher.await_count, 2)
        for index, request in enumerate(self.backend.requests):
            value = decoded_context(request)
            self.assertEqual(value["user"]["id"], "channel-owner")
            self.assertEqual((value["app"], value["space"]),
                             ("channel003", "channel") if index < 2 else (None, None))
            for secret in ("owned-key", "forged-key", "browser-fixture"):
                self.assertNotIn(secret, json.dumps(value))


class ExecutionContextPtcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-execution-context-ptc-", dir=Path(__file__).resolve().parents[2],
        )
        self.addCleanup(temporary.cleanup)
        self.backend = RecordingContextBackend()
        _python, self.skill = execution_toolsets(Path(temporary.name) / "context-fixture", self.backend)
        self.resolver = SimpleNamespace(profile_for_headers=AsyncMock(return_value=context_profile()))
        resolver_patch = patch(
            "deep_agent.sandbox.context.get_current_user_profile_resolver", return_value=self.resolver,
        )
        self.get_resolver = resolver_patch.start()
        self.addCleanup(resolver_patch.stop)

    def _agent(self, plan):
        def respond(messages, info):
            tools = {tool.name: tool for tool in info.function_tools}
            self.assertTrue({"run_code", "execute_python", "execute_skill_script"} <= set(tools))
            for name in ("execute_python", "execute_skill_script"):
                schema = tools[name].parameters_json_schema
                self.assertEqual(schema["properties"]["include_context"]["type"], "boolean")
                self.assertIs(schema["properties"]["include_context"]["default"], False)
                self.assertNotIn("context_env", schema["properties"])
            completed = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
            if completed < len(plan):
                name, args = plan[completed]
                return ModelResponse(parts=[ToolCallPart(name, args, f"step-{completed}")])
            return ModelResponse(parts=[TextPart("done")])

        registry = build_registry(sandbox_backend=self.backend)
        spec = AgentSpec(
            name="execution-context-ptc", model="test", instructions="Exercise explicit execution context opt-in.",
            capabilities=["builtin:sandbox", "builtin:ptc"], skills=["context-fixture"],
        )
        # Use production assembly for the lifecycle and native/PTC wrappers;
        # replace only the model response and the sandbox execution backend.
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
            return build_agent(spec, registry, skill_script_toolset=self.skill)

    def _assert_safe_return(self, result, *, allow_environment_name=False):
        for part in tool_returns(result):
            rendered = json.dumps({"content": part.content, "metadata": part.metadata}, ensure_ascii=False)
            for marker in (CONTEXT_USERNAME, CONTEXT_NICKNAME, CONTEXT_EMAIL, CONTEXT_AVATAR,
                           "synthetic-context-credential", "synthetic-context-cookie"):
                self.assertNotIn(marker, rendered)
            if not allow_environment_name:
                self.assertNotIn("DEEP_AGENT_CONTEXT", rendered)

    async def test_native_opt_in_then_monty_skill_opt_in_share_the_same_run_snapshot(self):
        code = (
            "skill = await execute_skill_script(skill_code='context-fixture', "
            "script_path='scripts/context.py', include_context=True)\n"
            "plain = await execute_python(code=\"{'answer': 42}\", include_context=False)\n"
            "assert skill['status'] == 'ok'\n"
            "assert plain['status'] == 'ok'\n"
            "{'total': skill['result']['answer'] + plain['result']['answer']}"
        )
        deps = context_deps()
        agent = self._agent([
            python_call(include_context=True), ("run_code", {"code": code}), skill_call(),
        ])
        result = await agent.run("Exercise native then Monty calls.", deps=deps)
        self.assertEqual(result.output, "done")
        self.assertEqual(len(self.backend.requests), 4)
        first, second, third, fourth = self.backend.requests
        self.assertIsNone(first.script_entry)
        self.assertEqual(second.script_entry, "scripts/context.py")
        self.assertEqual(decoded_context(first), decoded_context(second))
        self.assertEqual(third.context_env, {})
        self.assertEqual(fourth.context_env, {})
        self.resolver.profile_for_headers.assert_awaited_once()
        ptc = next(part for part in tool_returns(result) if part.tool_name == "run_code")
        self.assertEqual(ptc.outcome, "success")
        self.assertEqual(ptc.content, {"total": 84})
        self.assertEqual([step["tool_name"] for step in ptc.metadata["ptc_diagnostics"]["steps"]],
                         ["execute_skill_script", "execute_python"])
        self._assert_safe_return(result)
        self.assertIsNone(deps.execution_context_state)

    async def test_monty_opt_in_then_native_skill_reuses_snapshot_and_false_remains_empty(self):
        code = (
            "enabled = await execute_python(code=\"{'answer': 42}\", include_context=True)\n"
            "disabled = await execute_skill_script(skill_code='context-fixture', "
            "script_path='scripts/context.py')\n"
            "enabled['result']['answer'] + disabled['result']['answer']"
        )
        agent = self._agent([
            ("run_code", {"code": code}), skill_call(include_context=True), python_call(include_context=False),
        ])
        result = await agent.run("Exercise Monty then native calls.", deps=context_deps())
        self.assertEqual(len(self.backend.requests), 4)
        enabled, disabled, native, native_disabled = self.backend.requests
        self.assertEqual(decoded_context(enabled), decoded_context(native))
        self.assertEqual(disabled.context_env, {})
        self.assertEqual(native_disabled.context_env, {})
        self.resolver.profile_for_headers.assert_awaited_once()
        ptc = next(part for part in tool_returns(result) if part.tool_name == "run_code")
        self.assertEqual(ptc.outcome, "success")
        self.assertEqual(ptc.content, 84)
        self._assert_safe_return(result)

    async def test_monty_omitted_and_false_never_resolve_or_inject_context(self):
        code = (
            "a = await execute_python(code='42')\n"
            "b = await execute_skill_script(skill_code='context-fixture', script_path='scripts/context.py')\n"
            "c = await execute_python(code='42', include_context=False)\n"
            "d = await execute_skill_script(skill_code='context-fixture', "
            "script_path='scripts/context.py', include_context=False)\n"
            "a['status'] == b['status'] == c['status'] == d['status'] == 'ok'"
        )
        result = await self._agent([("run_code", {"code": code})]).run("Exercise default context policy.", deps=context_deps())
        self.assertEqual(len(self.backend.requests), 4)
        self.assertTrue(all(request.context_env == {} for request in self.backend.requests))
        self.get_resolver.assert_not_called()
        self.resolver.profile_for_headers.assert_not_awaited()
        self.assertIs(tool_returns(result)[0].content, True)
        self._assert_safe_return(result)

    async def test_monty_cannot_read_host_execution_context_after_native_opt_in(self):
        agent = self._agent([
            python_call(include_context=True), ("run_code", {"code": "DEEP_AGENT_CONTEXT"}),
        ])
        result = await agent.run("Check the Monty namespace boundary.", deps=context_deps())
        self.assertEqual(len(self.backend.requests), 1)
        decoded_context(self.backend.requests[0])
        self.resolver.profile_for_headers.assert_awaited_once()
        ptc = next(part for part in tool_returns(result) if part.tool_name == "run_code")
        self.assertEqual(ptc.outcome, "failed")
        self.assertIn("DEEP_AGENT_CONTEXT", str(ptc.content))
        self._assert_safe_return(result, allow_environment_name=True)

    async def test_reusing_one_agent_for_distinct_users_does_not_share_native_or_monty_context(self):
        async def fetch(user, *_args, **_kwargs):
            return context_profile(user, username=f"account-{user}")

        self.resolver.profile_for_headers.side_effect = fetch
        code = (
            "value = await execute_skill_script(skill_code='context-fixture', "
            "script_path='scripts/context.py', include_context=True)\n"
            "value['result']['answer']"
        )
        agent = self._agent([python_call(include_context=True), ("run_code", {"code": code})])
        for user in ("first-user", "second-user"):
            deps = context_deps(user, run_id=f"ptc-{user}")
            result = await agent.run("Exercise independent user runs.", deps=deps)
            self.assertEqual(result.output, "done")
            self._assert_safe_return(result)
            self.assertIsNone(deps.execution_context_state)
        self.assertEqual(self.resolver.profile_for_headers.await_count, 2)
        self.assertEqual(len(self.backend.requests), 4)
        for request, user in zip(self.backend.requests, ("first-user", "first-user", "second-user", "second-user")):
            context = decoded_context(request)
            self.assertEqual(context["user"]["id"], user)
            self.assertEqual(context["user"]["username"], f"account-{user}")


if __name__ == "__main__":
    unittest.main()
