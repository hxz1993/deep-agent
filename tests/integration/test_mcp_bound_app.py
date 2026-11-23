"""Exercise the bound route through the actual SDK and MCP HTTP protocol."""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException, Request
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_discovery import DeepAgentToolSearch, build_mcp_discovery_toolset, search_deferred_tools
from deep_agent.agent.tool_names import ScopedToolset
from deep_agent.api.v1.mcp import validate_bound_mcp_import
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.resources import build_mcp_resource_toolset
from deep_agent.capabilities.mcp.snapshot_toolset import SnapshotMcpToolset
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package
from deep_agent.schemas.mcp import McpToolSnapshot, McpToolSnapshotDefinition
from deep_agent.services.integration_app_access import IntegrationAppAccessError, ManagedApp, PlatformAppAccessClient
from deep_agent.services.mcp_bound_app import (
    bound_mcp_headers, bound_mcp_target, restricted_mcp_http_client, verify_bound_tool_args,
)

BASE = "https://platform.example/deepfos-server/"
URL = BASE + "deepfos-mcp/mcp"
TARGETS = {
    "budget": ("demo001", "demo", "年度预算"),
    "sales": ("demo002", "demo", "销售分析"),
    "ops": ("other001", "other", "经营分析"),
}
SNAPSHOT = McpToolSnapshot(definitions=[
    McpToolSnapshotDefinition(name=name, description="Read connection status." if name == "system_ping" else "A fixture operation.",
                              input_schema={"type": "object", "properties": {}})
    for name in ("system_ping", "cube_info", "element_list", "folder_list")
])


def config(name):
    app, space, label = TARGETS[name]
    return {
        "url": URL,
        "request_context": {
            "mode": "bound_app", "target_app_id": app, "target_space_id": space,
            "target_app_name": label, "target_space_name": space,
        },
    }


def deps(user):
    return AgentDeps(
        session_id=user, user=user, app_id="entry001", space_id="entry",
        request_headers={"app": "entry001", "space": "entry", "authorization": "ignored"},
        execution_request_headers={"user": "ignored", "authorization": f"Bearer test-{user}"},
    )


def run_context(dependencies):
    return RunContext(deps=dependencies, model=FunctionModel(lambda *_: None), usage=RunUsage())


def toolsets():
    return [
        ScopedToolset(SnapshotMcpToolset(name, config(name), SNAPSHOT, 1, 0), "mcp", name)
        for name in TARGETS
    ]


class McpBoundAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        base = patch("deep_agent.services.mcp_bound_app.PLATFORM_OPTION", SimpleNamespace(server=SimpleNamespace(base=BASE)))
        base.start()
        self.addCleanup(base.stop)

    async def test_real_sdk_three_targets_native_ptc_and_two_users(self):
        requests = []
        auth_checks = []
        rpc_methods = []
        connections = []
        resource_calls = []

        async def check(access, service, path, *, app=None, space=None, params=None):
            auth_checks.append((access._headers["user"], app, space))
            self.assertEqual((service, path), ("system", "check-permission-by-space-app"))
            return {"app": True, "space": True}

        async def server(request):
            self.assertEqual(str(request.url), URL)
            if request.method != "POST":
                return httpx.Response(405)
            rpc = json.loads(request.content)
            method = rpc["method"]
            rpc_methods.append(method)
            headers = request.headers
            self.assertEqual(headers["authorization"], f"Bearer test-{headers['user']}")
            self.assertEqual(headers["language"], "zh-cn")
            if method == "initialize":
                connections.append((headers["user"], headers["app"]))
                value = {"protocolVersion": rpc["params"]["protocolVersion"], "capabilities": {"tools": {}, "resources": {}},
                         "serverInfo": {"name": "fixture", "version": "1"}}
            elif method == "resources/list":
                resource_calls.append((headers["user"], headers["app"], headers["space"]))
                value = {"resources": [{"uri": "fixture://readme", "name": "Readme"}]}
            elif method == "tools/list":
                value = {"tools": [{"name": "system_ping", "inputSchema": {"type": "object", "properties": {}}}]}
            elif method == "tools/call":
                self.assertEqual(rpc["params"]["name"], "system_ping")
                marker = (headers["user"], headers["app"], headers["space"])
                requests.append(marker)
                value = {"content": [], "structuredContent": {"app": marker[1], "space": marker[2]}}
            else:
                return httpx.Response(202)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": value})

        def restricted(url, headers):
            return restricted_mcp_http_client(url, headers, transport=httpx.MockTransport(server))

        def respond(messages, info):
            calls = [part.tool_name for message in messages for part in message.parts if isinstance(part, ToolCallPart)]
            if not calls:
                self.assertIn("search_tools", {t.name for t in info.function_tools})
                return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": [
                    "mcp__budget__system_ping", "mcp__sales__system_ping", "mcp__ops__system_ping",
                ]}, "search")])
            if calls == ["search_tools"]:
                names = {tool.name for tool in info.function_tools}
                self.assertTrue({f"mcp__{role}__system_ping" for role in TARGETS} <= names)
                return ModelResponse(parts=[
                    ToolCallPart("mcp__budget__system_ping", {}, "native"),
                    ToolCallPart("run_code", {"code": (
                        "import asyncio\n"
                        "out = await asyncio.gather(budget_system_ping(), sales_system_ping(), ops_system_ping())\n"
                        "out"
                    )}, "ptc"),
                ])
            if "list_mcp_resources" not in calls:
                return ModelResponse(parts=[ToolCallPart("list_mcp_resources", {"server_name": "budget"}, "resource")])
            return ModelResponse(parts=[TextPart("done")])

        scoped = toolsets()
        discovery = build_mcp_discovery_toolset(
            scoped, server_names=tuple(TARGETS), estimator=TokenEstimator.conservative(),
        )
        resources = build_mcp_resource_toolset(
            AgentSpec(name="test", model="test", instructions="", capabilities=["mcp:budget"]),
            [SimpleNamespace(name="budget", transport="url", config=config("budget"),
                             inspection={"status": "healthy", "resources": {"status": "supported", "count": 1}})],
        )
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps,
                      toolsets=[resources, build_code_mode_tool_package().toolset, FilePayloadResolvingToolset(discovery)],
                      capabilities=[DeepAgentToolSearch(strategy=search_deferred_tools), HybridCodeMode(tools=[], dynamic_catalog=True)])
        with patch.object(PlatformAppAccessClient, "_get", check), patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.restricted_mcp_http_client", restricted,
        ):
            try:
                async with asyncio.timeout(10):
                    results = await asyncio.gather(*(agent.run("check the connections", deps=deps(user))
                                                     for user in ("alice", "bob")))
            except TimeoutError:
                self.fail(f"MCP fixture timed out after RPC methods {rpc_methods}")
        self.assertEqual([result.output for result in results], ["done", "done"])
        for user in ("alice", "bob"):
            self.assertCountEqual([entry for entry in requests if entry[0] == user], [
                (user, app, space) for app, space, _ in TARGETS.values()
            ] + [(user, "demo001", "demo")])
        self.assertCountEqual(resource_calls, [(user, "demo001", "demo") for user in ("alice", "bob")])
        self.assertCountEqual(connections, [
            (user, app) for user in ("alice", "bob") for app, _, _ in TARGETS.values()
        ])
        self.assertCountEqual(auth_checks, [
            (user, app, space) for user in ("alice", "bob") for app, space, _ in TARGETS.values()
        ])

    async def test_exact_names_bare_names_and_source_ranking(self):
        ctx = run_context(deps("alice"))
        definitions = []
        for toolset in toolsets():
            definitions.extend(tool.tool_def for tool in (await toolset.get_tools(ctx)).values())
        def search(query):
            return search_deferred_tools(ctx, [query], definitions)

        self.assertEqual(search("mcp__sales__system_ping"), ["mcp__sales__system_ping"])
        self.assertCountEqual(search("system_ping"), [f"mcp__{name}__system_ping" for name in TARGETS])
        self.assertEqual(search("mcp__missing__system_ping"), [])
        self.assertEqual(search("销售分析 system_ping")[0], "mcp__sales__system_ping")
        self.assertEqual(search("经营分析 system_ping")[0], "mcp__ops__system_ping")

    async def test_denied_missing_key_only_and_unavailable_never_connect(self):
        for login in ({}, {"x-deepfos-key": "scoped-key"}, {"x-deepfos-key": "key", "authorization": "Bearer mixed"}):
            dependencies = deps("alice")
            dependencies.execution_request_headers = login
            with self.assertRaisesRegex(ToolFailed, "平台登录"):
                await bound_mcp_headers(config("budget"), dependencies)

        for data, error in (({"app": True, "space": False}, "无权"), ({"app": True}, "未返回")):
            with patch.object(PlatformAppAccessClient, "_get", AsyncMock(return_value=data)):
                with self.assertRaisesRegex(ToolFailed, error):
                    await bound_mcp_headers(config("budget"), deps("alice"))
        with patch.object(PlatformAppAccessClient, "_get", AsyncMock(side_effect=IntegrationAppAccessError(503, "权限校验超时"))):
            with self.assertRaisesRegex(ToolFailed, "超时"):
                await bound_mcp_headers(config("budget"), deps("alice"))

    async def test_configuration_import_revalidates_owner_and_replaces_display_names(self):
        request = Request({"type": "http", "headers": [(b"user", b"alice"), (b"authorization", b"Bearer fixture")]})
        row = {"transport": "url", "config": config("budget")}
        directory = SimpleNamespace(verify=AsyncMock(return_value=ManagedApp(
            "demo001", "demo", "tenant", "Verified App", "Enterprise",
        )))
        with patch("deep_agent.api.v1.mcp._bound_directory", return_value=(directory, {})), patch(
            "deep_agent.api.v1.mcp.resolve_scope_directory",
            AsyncMock(return_value=SimpleNamespace(spaces={"demo": "Verified Space"})),
        ):
            await validate_bound_mcp_import(request, [row])
            saved = row["config"]["request_context"]
            self.assertEqual((saved["target_app_name"], saved["target_space_name"]), ("Verified App", "Verified Space"))
            directory.verify.side_effect = IntegrationAppAccessError(404, "应用不存在或当前账号无权管理。")
            with self.assertRaises(HTTPException) as denied:
                await validate_bound_mcp_import(request, [row])
            self.assertEqual(denied.exception.status_code, 404)
        invalid = {"transport": "url", "config": {**config("budget"), "url": URL + "?unexpected=1"}}
        with self.assertRaises(HTTPException) as rejected:
            await validate_bound_mcp_import(request, [invalid])
        self.assertEqual(rejected.exception.status_code, 422)

    async def test_no_redirect_and_no_scope_override(self):
        seen = []

        def redirect(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://other.example/"})

        async with restricted_mcp_http_client(URL, {}, transport=httpx.MockTransport(redirect)) as client:
            with self.assertRaisesRegex(httpx.HTTPStatusError, "redirect"):
                await client.get(URL, follow_redirects=True)
            with self.assertRaisesRegex(ValueError, "destination"):
                await client.get("https://other.example/")
        self.assertEqual(seen, [URL])
        for url in ("http://platform.example/deepfos-server/deepfos-mcp/mcp", URL + "?other=1", URL + "/",
                    "https://other.example/deepfos-server/deepfos-mcp/mcp"):
            with self.subTest(url=url), self.assertRaises(IntegrationAppAccessError):
                bound_mcp_target({**config("budget"), "url": url})
        for args in ({"app": "demo002"}, {"appId": "other001"}, {"space_id": "other"}):
            with self.subTest(args=args), self.assertRaisesRegex(ToolFailed, "不能替换"):
                verify_bound_tool_args(config("budget"), args)
        verify_bound_tool_args(config("budget"), {"app": "demo001", "space": "", "other": "other001"})
