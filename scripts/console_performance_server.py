"""Local-only Console rendering replay; never connects to a database or model."""
from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8046)
    parser.add_argument("--baseline-root", type=Path, help="Private snapshot containing console/")
    parser.add_argument("--fixture-json", type=Path, help="Optional generated protocol fixture for isolated full-Console checks")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture_root = root / "tests/frontend/console"
    replay = json.loads(args.fixture_json.read_text()) if args.fixture_json else None
    stop_requested = threading.Event()
    run_state = {"status": "idle"}
    files = {
        "local_replay_json": (b'{"status":"ok","rows":31} \n', "application/json", "replay-result.json"),
        "local_replay_txt": (b"Deterministic report.\n", "text/plain", "replay-result.txt"),
        "local_replay_png": (base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGP4/x8AAwAB//wl3FEAAAAASUVORK5CYII="), "image/png", "replay-image.png"),
    }

    class Handler(SimpleHTTPRequestHandler):
        def reply_json(self, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def proxy_read(self, body: bytes | None = None) -> None:
            # Only read-only routes reach this helper; no configuration/chat writes.
            headers = {key: value for key, value in self.headers.items() if key.lower() in {"user", "app", "space", "language", "authorization", "cookie", "x-deep-agent-app", "content-type"}}
            request = Request("http://127.0.0.1:8000" + self.path, data=body, headers=headers, method=self.command)
            try:
                response = urlopen(request, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                content = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 1024 * 1024))
            if replay and path == "/api/v1/chat/stream":
                request = json.loads(body)
                text = str(request.get("message") or request.get("text") or request.get("prompt") or "")
                run_state["status"] = "running"
                stop_requested.clear()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    frames = [frame for frame in replay["fixture"]["frames"] if frame["event"] not in {"replay_user", "session", "final"}]
                    frames.insert(0, {"event": "session", "data": {"session_id": "console-replay-session", "run_id": "console-replay-run"}})
                    frames.append({"event": "final", "data": {"status": "completed", "output": replay["fixture"]["turns"][-1]["answer"], "run_id": "console-replay-run"}})
                    for index, frame in enumerate(frames):
                        if stop_requested.is_set():
                            frame = {"event": "cancelled", "data": {"status": "cancelled", "run_id": "console-replay-run"}}
                        data = {**frame["data"], "run_id": "console-replay-run"}
                        self.wfile.write(f'event: {frame["event"]}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode())
                        self.wfile.flush()
                        if stop_requested.is_set():
                            break
                        if "replay-drop" in text and index == len(frames) // 2:
                            break
                        if index % 32 == 0:
                            time.sleep(.012)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    run_state["status"] = "idle"
                return
            if replay and path == "/api/v1/sessions/console-replay-session/cancel":
                stop_requested.set()
                self.reply_json({"status": "cancel_requested"})
                return
            if replay and path.startswith("/api/v1/diagnostics/") and any(part in path for part in ("/query", "/overview", "/sessions", "/logs", "/usage")):
                self.proxy_read(body)
                return
            self.send_error(405, "Read-only replay server")

        def do_GET(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if replay and path.startswith("/api/v1/sessions/console-replay-session"):
                if path.endswith("/run-status"):
                    self.reply_json({**run_state, "run_id": "console-replay-run" if run_state["status"] != "idle" else None})
                elif path.endswith("/history"):
                    self.reply_json({"schema_version": 1, "session_id": "console-replay-session", "event_head_sequence": 51, "items": replay["conversation"]})
                elif path.endswith("/events"):
                    self.reply_json({**replay["history"], "next_after_sequence": None})
                else:
                    self.reply_json({"id": "console-replay-session", "agent_id": "replay", "title": "Console 脱敏回放", "status": run_state["status"]})
                return
            if path == "/":
                self.send_response(302)
                query = urlsplit(self.path).query
                self.send_header("Location", "/replay/performance-replay.html" + ("?" + query if query else ""))
                self.end_headers()
                return
            identity = path.removeprefix("/files/") if path.startswith("/files/") else (
                path.split("/")[-2] if path.startswith("/api/v1/local-files/") and path.endswith("/download") else None
            )
            if identity in files:
                content, mime, name = files[identity]
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if replay and (path.startswith("/api/v1/") or path in {"/info", "/health"}):
                self.proxy_read()
                return
            options = [("/console/", root / "console"), ("/vendor/", root / "console/vendor"), ("/replay/", fixture_root)]
            if args.baseline_root:
                options.append(("/baseline/console/", args.baseline_root.resolve() / "console"))
            for prefix, directory in options:
                if not path.startswith(prefix):
                    continue
                target = (directory / path.removeprefix(prefix)).resolve()
                if prefix == "/console/" and path == prefix:
                    target = directory / "index.html"
                if not target.is_relative_to(directory.resolve()) or not target.is_file():
                    self.send_error(404)
                    return
                if prefix == "/replay/" and not target.name.startswith("performance-"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(str(target)))
                self.send_header("Content-Length", str(target.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(target.read_bytes())
                return
            self.send_error(404)

        def log_message(self, format: str, *values: object) -> None:
            pass

    class ReplayServer(ThreadingHTTPServer):
        request_queue_size = 128  # ES modules and styles arrive in a startup burst.

    server = ReplayServer(("127.0.0.1", args.port), Handler)
    print(f"Console replay: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
