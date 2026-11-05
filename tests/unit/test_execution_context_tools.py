"""Behavioral contracts for explicit Python/Skill execution context opt-in."""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities import build_registry
from deep_agent.sandbox.types import BackendHealth, ExecuteRequest, ExecuteResult, TruncationMetadata
from deep_agent.security.current_user import CurrentUserProfile, CurrentUserUnavailableError
from deep_agent.skills.runtime import ResolvedSkill
from deep_agent.skills.script_tool import build_skill_script_toolset


CONTEXT_USER = "execution-context-user"
CONTEXT_USERNAME = "context-account"
CONTEXT_NICKNAME = "执行上下文测试用户"
CONTEXT_EMAIL = "context-account@example.test"
CONTEXT_AVATAR = "https://example.test/context-avatar.png"
CONTEXT_KEYS = {"user", "app", "space", "language", "resolution", "error"}
USER_KEYS = {"id", "username", "nickname", "display_name", "email", "avatar_url"}


class RecordingContextBackend:
    """Keep the real tool/SDK dispatch; replace only sandbox process execution."""

    def __init__(self) -> None:
        self.requests: list[ExecuteRequest] = []
        self.health = AsyncMock(return_value=BackendHealth(
            enabled=True, backend="context-fixture", state_scope="request", security_level="none",
        ))
        self.aclose = AsyncMock()

    async def execute(self, request: ExecuteRequest) -> ExecuteResult:
        self.requests.append(request)
        return ExecuteResult(
            request_id=request.request_id, status="ok", exit_code=0,
            stdout="", stderr="", result={"answer": 42}, error=None, duration_ms=1,
            backend="context-fixture", environment="test", security_level="none",
            memory_peak_bytes=None, truncated=TruncationMetadata(),
        )


def context_deps(user: str = CONTEXT_USER, *, run_id: str = "context-run") -> AgentDeps:
    return AgentDeps(
        session_id="execution-context", run_id=run_id, user=user,
        app_id="context001", space_id="context",
        request_headers={
            "user": user, "app": "context001", "space": "context", "language": "zh_CN",
            "authorization": "Bearer synthetic-context-credential",
            "cookie": "sid=synthetic-context-cookie",
        },
    )


def context_profile(user: str = CONTEXT_USER, *, username: str = CONTEXT_USERNAME) -> CurrentUserProfile:
    return CurrentUserProfile(
        user_id=user, username=username, nickname=CONTEXT_NICKNAME,
        email=CONTEXT_EMAIL, avatar_url=CONTEXT_AVATAR,
    )


def execution_toolsets(root: Path, backend: RecordingContextBackend):
    """Make one minimally indexed Skill with the same hash contract as production."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: context-fixture\ndescription: Synthetic execution context fixture\n---\n",
        encoding="utf-8",
    )
    source = b"# The recording backend intentionally does not execute this script.\n"
    (root / "scripts/context.py").write_bytes(source)
    item = ResolvedSkill(
        code="context-fixture", source="upload", package_root=root, library_root=root.parent,
        resource_index=[{
            "path": "scripts/context.py", "category": "script",
            "content_hash": hashlib.sha256(source).hexdigest(),
        }],
    )
    python = build_registry(sandbox_backend=backend).get("builtin:sandbox").toolset
    skill = build_skill_script_toolset({item.code: item}, backend=backend, timeout_sec=5)
    assert skill is not None
    return python, skill


def python_call(*, include_context: bool | None = None, **kwargs):
    args = {"code": "{'answer': 42}", **kwargs}
    if include_context is not None:
        args["include_context"] = include_context
    return "execute_python", args


def skill_call(*, include_context: bool | None = None, **kwargs):
    args = {"skill_code": "context-fixture", "script_path": "scripts/context.py", **kwargs}
    if include_context is not None:
        args["include_context"] = include_context
    return "execute_skill_script", args


def tool_returns(result):
    return [part for message in result.new_messages() for part in message.parts
            if isinstance(part, ToolReturnPart)]


def decoded_context(request: ExecuteRequest):
    assert set(request.context_env) == {"DEEP_AGENT_CONTEXT"}, request.context_env
    return json.loads(request.context_env["DEEP_AGENT_CONTEXT"])


class ExecutionContextToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-execution-context-tools-", dir=Path(__file__).resolve().parents[2],
        )
        self.addCleanup(temporary.cleanup)
        self.backend = RecordingContextBackend()
        self.toolsets = execution_toolsets(Path(temporary.name) / "context-fixture", self.backend)
        self.resolver = SimpleNamespace(profile_for_headers=AsyncMock(return_value=context_profile()))
        self.resolver_patch = patch(
            "deep_agent.sandbox.context.get_current_user_profile_resolver", return_value=self.resolver,
        )
        self.get_resolver = self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)
        self.schemas = []
        self.descriptions = []

    async def _run_calls(self, calls, *, deps=None, parallel=False):
        from deep_agent.sandbox.context import ExecutionContextLifecycle

        step = 0

        def respond(_messages, info):
            nonlocal step
            self.schemas.append({tool.name: tool.parameters_json_schema for tool in info.function_tools})
            self.descriptions.append({tool.name: tool.description or "" for tool in info.function_tools})
            if parallel and step == 0:
                step += 1
                return ModelResponse(parts=[
                    ToolCallPart(name, args, f"parallel-{index}") for index, (name, args) in enumerate(calls)
                ])
            if not parallel and step < len(calls):
                name, args = calls[step]
                step += 1
                return ModelResponse(parts=[ToolCallPart(name, args, f"call-{step}")])
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(
            FunctionModel(respond), deps_type=AgentDeps, toolsets=list(self.toolsets),
            capabilities=[ExecutionContextLifecycle()],
        )
        return await agent.run("Exercise explicit context opt-in.", deps=deps or context_deps())

    def _assert_no_automatic_context_return(self, result):
        returned = tool_returns(result)
        self.assertTrue(returned)
        for part in returned:
            self.assertEqual(part.outcome, "success")
            self.assertEqual(part.content["result"], {"answer": 42})
            rendered = json.dumps({"content": part.content, "metadata": part.metadata}, ensure_ascii=False)
            for private in (
                "DEEP_AGENT_CONTEXT", CONTEXT_USERNAME, CONTEXT_NICKNAME, CONTEXT_EMAIL, CONTEXT_AVATAR,
                "synthetic-context-credential", "synthetic-context-cookie",
            ):
                self.assertNotIn(private, rendered)

    def _assert_resolved(self, request, *, user=CONTEXT_USER, username=CONTEXT_USERNAME):
        value = decoded_context(request)
        self.assertEqual(set(value), CONTEXT_KEYS)
        self.assertEqual(set(value["user"]), USER_KEYS)
        self.assertEqual(value["user"], {
            "id": user, "username": username, "nickname": CONTEXT_NICKNAME,
            "display_name": username, "email": CONTEXT_EMAIL, "avatar_url": CONTEXT_AVATAR,
        })
        self.assertEqual(value["app"], "context001")
        self.assertEqual(value["space"], "context")
        self.assertEqual(value["language"], "zh_CN")
        self.assertEqual(value["resolution"], "resolved")
        self.assertIsNone(value["error"])
        self.assertNotIn("synthetic-context-credential", request.context_env["DEEP_AGENT_CONTEXT"])
        self.assertNotIn("synthetic-context-cookie", request.context_env["DEEP_AGENT_CONTEXT"])
        return value

    async def test_sdk_exposes_optional_boolean_opt_in_without_host_environment_input(self):
        result = await self._run_calls([python_call(), skill_call()])
        for name in ("execute_python", "execute_skill_script"):
            schema = self.schemas[0][name]
            option = schema["properties"]["include_context"]
            self.assertEqual(option["type"], "boolean")
            self.assertIs(option["default"], False)
            self.assertNotIn("include_context", schema.get("required", []))
            self.assertFalse({"env", "context_env", "user", "username", "local_username", "execution_request_headers"}
                             & set(schema["properties"]))
            description = self.descriptions[0][name]
            self.assertIn('json.loads(os.environ["DEEP_AGENT_CONTEXT"])', description)
            example, _ = json.JSONDecoder().raw_decode(description[description.index('{"user":'):])
            self.assertEqual(set(example), CONTEXT_KEYS)
            self.assertEqual(set(example["user"]), USER_KEYS)
            self.assertIn("unavailable", description)
        self.assertEqual(len(self.backend.requests), 2)
        self._assert_no_automatic_context_return(result)

    async def test_omitted_and_false_never_query_or_inject_context_for_either_tool(self):
        result = await self._run_calls([
            python_call(), skill_call(), python_call(include_context=False), skill_call(include_context=False),
        ])
        self.assertEqual([request.context_env for request in self.backend.requests], [{}, {}, {}, {}])
        self.get_resolver.assert_not_called()
        self.resolver.profile_for_headers.assert_not_awaited()
        self._assert_no_automatic_context_return(result)

    async def test_python_and_skill_opt_in_share_one_context_but_later_false_stays_empty(self):
        inputs = {"DEEP_AGENT_CONTEXT": "model-input", "user": {"id": "untrusted-input-user"}}
        result = await self._run_calls([
            python_call(include_context=True, inputs=inputs), skill_call(include_context=True),
            python_call(), skill_call(include_context=False),
        ])
        self.assertEqual(len(self.backend.requests), 4)
        first, second, third, fourth = self.backend.requests
        self.assertEqual(self._assert_resolved(first), self._assert_resolved(second))
        self.assertEqual(first.inputs, inputs)
        self.assertEqual(third.context_env, {})
        self.assertEqual(fourth.context_env, {})
        self.resolver.profile_for_headers.assert_awaited_once()
        self._assert_no_automatic_context_return(result)

    async def test_parallel_sdk_python_and_skill_opt_in_are_singleflight(self):
        async def fetch(*_args, **_kwargs):
            await asyncio.sleep(.02)
            return context_profile()

        self.resolver.profile_for_headers.side_effect = fetch
        result = await self._run_calls(
            [python_call(include_context=True), skill_call(include_context=True)], parallel=True,
        )
        self.assertEqual(len(self.backend.requests), 2)
        self.resolver.profile_for_headers.assert_awaited_once()
        for request in self.backend.requests:
            self._assert_resolved(request)
        self._assert_no_automatic_context_return(result)

    async def test_resolver_failure_is_safe_cached_context_and_does_not_fail_execution(self):
        marker = "private-upstream-error-with-cookie-and-customer-body"
        self.resolver.profile_for_headers.side_effect = CurrentUserUnavailableError(marker)
        result = await self._run_calls([python_call(include_context=True), skill_call(include_context=True)])
        self.assertEqual(len(self.backend.requests), 2)
        self.resolver.profile_for_headers.assert_awaited_once()
        values = [decoded_context(request) for request in self.backend.requests]
        self.assertEqual(values[0], values[1])
        value = values[0]
        self.assertEqual(set(value), CONTEXT_KEYS)
        self.assertEqual(value["user"], {key: CONTEXT_USER if key == "id" else None for key in USER_KEYS})
        for field in ("app", "space", "language"):
            self.assertIsNone(value[field])
        self.assertEqual(value["resolution"], "unavailable")
        self.assertEqual(set(value["error"]), {"code", "message"})
        self.assertTrue(value["error"]["code"])
        self.assertTrue(value["error"]["message"])
        self.assertNotIn(marker, json.dumps(value))
        self._assert_no_automatic_context_return(result)

    async def test_tool_inputs_cannot_replace_the_trusted_execution_user(self):
        deps = context_deps()
        deps.request_headers["user"] = "untrusted-header-user"
        result = await self._run_calls([
            python_call(include_context=True, inputs={"user": "untrusted-input-user", "username": "spoofed"}),
        ], deps=deps)
        self._assert_resolved(self.backend.requests[0])
        self.assertEqual(self.resolver.profile_for_headers.await_args.args[0], CONTEXT_USER)
        self._assert_no_automatic_context_return(result)

    async def test_separate_user_runs_never_reuse_another_users_context(self):
        async def fetch(user, *_args, **_kwargs):
            return context_profile(user, username=f"account-{user}")

        self.resolver.profile_for_headers.side_effect = fetch
        for user in ("first-user", "second-user"):
            result = await self._run_calls(
                [python_call(include_context=True), skill_call(include_context=True)],
                deps=context_deps(user, run_id=f"run-{user}"),
            )
            self._assert_no_automatic_context_return(result)
        self.assertEqual(self.resolver.profile_for_headers.await_count, 2)
        self.assertEqual(len(self.backend.requests), 4)
        for request, user in zip(self.backend.requests, ("first-user", "first-user", "second-user", "second-user")):
            self._assert_resolved(request, user=user, username=f"account-{user}")


if __name__ == "__main__":
    unittest.main()
