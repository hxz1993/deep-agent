"""Pinned SDK multipart wire contract against an isolated local HTTP platform."""
import asyncio
import json
import unittest

from deep_agent.channels.adapters.feishu.outbound import send_component
from deep_agent.channels.outbound import MessageTarget, PreparedComponent


def target(**changes):
    return MessageTarget(**{"account_id": "a", "peer_type": "single", "peer_id": "ou_user",
                            "message_id": "om_inbound", "chat_id": "oc_current", **changes})


class OfficialHTTPTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_multipart_upload_and_reply_wire(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from lark_channel import FeishuChannel
        from lark_channel.channel.config import TransportConfig

        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append((self.path, self.headers.get("Content-Type"), body))
                if self.path.endswith("/tenant_access_token/internal"):
                    result = {"code": 0, "tenant_access_token": "fixture-token", "expire": 7200}
                elif self.path.endswith("/images"):
                    result = {"code": 0, "data": {"image_key": "img_wire"}}
                elif self.path.endswith("/files"):
                    result = {"code": 0, "data": {"file_key": "file_wire"}}
                else:
                    result = {"code": 0, "data": {"message_id": "om_wire"}}
                raw = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        sdk = FeishuChannel(app_id="cli_outbound_wire", app_secret="fixture-secret",
                            domain=f"http://127.0.0.1:{server.server_port}",
                            transport=TransportConfig(trust_env_proxy=False))
        try:
            for kind in ("image", "file", "audio", "video"):
                result = await send_component(sdk, target(thread_id="omt_thread"),
                    PreparedComponent(kind, data=b"fixture-content-123", filename=f"test.{kind}"))
                self.assertEqual(result.message_id, "om_wire")
            uploads = [call for call in calls if call[0].endswith(("/images", "/files"))]
            self.assertEqual(len(uploads), 4)
            for _, mime, body in uploads:
                self.assertIn("multipart/form-data", mime)
                self.assertIn(b"fixture-content-123", body)
            self.assertIn(b"opus", uploads[2][2]); self.assertIn(b"mp4", uploads[3][2])
            replies = [json.loads(body) for path, _, body in calls if path.endswith("/reply")]
            self.assertEqual(len(replies), 4)
            self.assertTrue(all(body["reply_in_thread"] for body in replies))
            self.assertEqual([body["msg_type"] for body in replies], ["image", "file", "audio", "media"])
            self.assertTrue(all(path.endswith("/om_inbound/reply") for path, _, _ in calls if path.endswith("/reply")))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close(); thread.join(timeout=2)
