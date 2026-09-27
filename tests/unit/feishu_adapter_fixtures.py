"""Isolated local platform fixture, never loaded by production code."""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


def local_sdk_worker(connection, account, infinite_retry):
    # Test-only coverage capture for dedicated SDK workers, which intentionally
    # use os._exit after public SDK shutdown to reclaim SDK-owned loop threads.
    import os
    original_exit = os._exit
    coverage_path = os.environ.get("FEISHU_TEST_COVERAGE_BASE")
    if coverage_path:
        from coverage import Coverage
        coverage = Coverage(data_file=coverage_path, data_suffix=True, branch=True,
                            source=["deep_agent.channels.adapters.feishu"])
        coverage.start()
        def save_and_exit(code):
            coverage.stop()
            coverage.save()
            original_exit(code)
        os._exit = save_and_exit
    from deep_agent.channels.adapters.feishu.worker import run_worker
    from deep_agent.channels.adapters.feishu.core import FeishuSDKAdapter
    from lark_channel import FeishuChannel
    def factory(values, retry):
        def client_factory(*, config):
            config.domain = values.settings["_test_domain"]
            config.transport.trust_env_proxy = False
            config.resolve_sender_names = False
            return FeishuChannel(config=config, name_lookup=lambda ids: {})
        with patch("deep_agent.channels.adapters.feishu.core.FeishuChannel", side_effect=client_factory):
            return FeishuSDKAdapter(values, retry)
    run_worker(connection, account, infinite_retry, adapter_factory=factory)


def orphan_gateway_entry(control, domain):
    from deep_agent.channels.adapters.feishu import FeishuAdapter
    async def run():
        adapter = FeishuAdapter(SimpleNamespace(id="orphan", bot_id="cli_orphan", secret="dummy",
                settings={"_test_domain": domain}), infinite_retry=False, _worker_target=local_sdk_worker)
        await adapter.start(lambda _: asyncio.sleep(0))
        await wait_connected(adapter)
        control.send(adapter._process.pid)
        try:
            await asyncio.Future()
        finally:
            await adapter.stop()
    asyncio.run(run())


async def wait_connected(adapter):
    async with asyncio.timeout(8):
        while not adapter.status()["authenticated"]:
            if adapter.status()["state"] == "failed":
                raise AssertionError(adapter.status())
            await asyncio.sleep(0.02)


class LocalFeishuPlatform:
    def __init__(self):
        self.connections = {}
        self.acks = asyncio.Queue()
        self.http_calls = []

    async def __aenter__(self):
        from lark_channel.ws.pb.pbbp2_pb2 import Frame
        async def connected(connection):
            app_id = parse_qs(urlparse(connection.request.path).query)["device_id"][0]
            self.connections[app_id] = connection
            try:
                async for data in connection:
                    frame = Frame(); frame.ParseFromString(data)
                    if frame.method == 1:
                        self.acks.put_nowait((app_id, json.loads(frame.payload)))
            except ConnectionClosed:
                pass
            finally:
                if self.connections.get(app_id) is connection:
                    self.connections.pop(app_id, None)
        self.ws = await serve(connected, "127.0.0.1", 0)
        ws_port = self.ws.sockets[0].getsockname()[1]
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                fixture.http_calls.append((self.path, request))
                if self.path == "/callback/ws/endpoint":
                    response = {"code": 0, "data": {"URL": f"ws://127.0.0.1:{ws_port}/?device_id={request['AppID']}&service_id=1",
                        "ClientConfig": {"PingInterval": 30, "ReconnectInterval": 1, "ReconnectNonce": 0, "ReconnectCount": 0}}}
                elif self.path.endswith("/tenant_access_token/internal"):
                    response = {"code": 0, "tenant_access_token": "local-token", "expire": 7200}
                elif self.path == "/open-apis/cardkit/v1/cards":
                    response = {"code": 0, "data": {"card_id": "card_local"}}
                elif self.path.endswith("/reply"):
                    response = {"code": 0, "data": {"message_id": "om_reply"}}
                else:
                    response = {"code": 0}
                self.respond(response)
            def do_GET(self):
                fixture.http_calls.append((self.path, None))
                if "/resources/" in self.path:
                    raw = b"real-sdk-resource-bytes"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self.respond({"code": 0, "bot": {"open_id": "ou_bot", "app_name": "Local"}})
            def do_PUT(self):
                self.do_POST()
            def do_PATCH(self):
                self.do_POST()
            def respond(self, response):
                raw = json.dumps(response).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.domain = f"http://127.0.0.1:{self.http.server_port}"
        return self

    async def __aexit__(self, *_):
        self.ws.close()
        await self.ws.wait_closed()
        await asyncio.to_thread(self.http.shutdown)
        self.http.server_close()
        self.thread.join(timeout=2)

    async def send(self, app_id, *, message_id="om_ws", text="真实 SDK 消息", chat_type="p2p", chat_id="oc_ws", sender_id="ou_human"):
        from lark_channel.ws.pb.pbbp2_pb2 import Frame
        frame = Frame(SeqID=1, LogID=1, service=1, method=1)
        for key, value in {"type": "event", "message_id": message_id, "sum": "1", "seq": "0", "trace_id": "test"}.items():
            header = frame.headers.add(); header.key = key; header.value = value
        frame.payload = json.dumps({"schema": "2.0", "header": {
            "event_id": "evt_" + message_id, "event_type": "im.message.receive_v1", "tenant_key": "tenant_ws",
            "create_time": str(int(time.time()*1000)), "app_id": app_id}, "event": {
            "sender": {"sender_id": {"open_id": sender_id}, "sender_type": "user", "tenant_key": "tenant_ws"},
            "message": {"message_id": message_id, "chat_id": chat_id, "chat_type": chat_type,
                        "message_type": "text", "content": json.dumps({"text": text}),
                        "create_time": str(int(time.time()*1000))}}}).encode()
        await self.connections[app_id].send(frame.SerializeToString())
