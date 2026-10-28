from __future__ import annotations

import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deep_agent.capabilities.mcp.inspection import inspect_mcp_server
from deep_agent.capabilities.mcp.toolset_builder import build_resource_client
from deep_agent.db.models import McpServer


@contextmanager
def optional_resource_server(resource_mode: str):
    """Real HTTP/SDK boundary: valid tools plus imperfect optional endpoints."""
    methods: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def send(self, status, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            self.send(405)

        def do_DELETE(self):
            self.send(204)

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = message["method"]
            methods.append(method)
            if "id" not in message:
                self.send(202)
                return
            response = {"jsonrpc": "2.0", "id": message["id"]}
            if method == "initialize":
                result = {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "optional-resource-fixture", "version": "1"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                if resource_mode == "tool_error":
                    response["error"] = {"code": -32603, "message": "tool catalog failed"}
                    self.send(200, response)
                    return
                result = {"tools": [{"name": "query", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": "query completed"}], "isError": False}
            elif method == "resources/list":
                if resource_mode == "malformed":
                    result = {"resources": {}}
                elif resource_mode == "missing":
                    response["error"] = {"code": -32601, "message": "Method not found"}
                    self.send(200, response)
                    return
                else:
                    result = {"resources": [{"name": "guide", "uri": "docs://guide"}]}
            elif method == "resources/templates/list":
                response["error"] = {"code": -32601, "message": "Method not found"}
                self.send(200, response)
                return
            else:
                self.send(400)
                return
            response["result"] = result
            self.send(200, response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", methods
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class OptionalResourceWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_endpoints_do_not_prevent_real_sdk_tools(self):
        for mode in ("malformed", "missing", "valid"):
            with self.subTest(mode=mode), optional_resource_server(mode) as (url, methods):
                config = {"url": url, "request_context": {"mode": "isolated"}}
                row = McpServer(name="compat", transport="url", config=config)
                result = await inspect_mcp_server(row)
                self.assertEqual(result.inspection.status, "healthy")
                self.assertEqual(result.inspection.tools.count, 1)
                self.assertEqual(result.inspection.tools.snapshot_status, "ready")
                self.assertEqual(result.inspection.resources.status, "supported" if mode == "valid" else "error")
                self.assertNotIn("resources/templates/list", methods)
                # A second real connection proves an optional parser/protocol
                # failure has not changed the native tool invocation contract.
                async with build_resource_client("compat", "url", config) as client:
                    output = await client.direct_call_tool("query", {})
                self.assertIn("query completed", str(output))
                self.assertEqual(methods.count("tools/call"), 1)
                self.assertNotIn("resources/templates/list", methods)

    async def test_real_tool_failure_still_degrades_inspection(self):
        with optional_resource_server("tool_error") as (url, methods):
            row = McpServer(name="compat", transport="url", config={"url": url})
            result = await inspect_mcp_server(row)
            self.assertEqual(result.inspection.status, "degraded")
            self.assertEqual(result.inspection.tools.status, "error")
            self.assertEqual(result.inspection.resources.status, "supported")
            self.assertNotIn("resources/templates/list", methods)
