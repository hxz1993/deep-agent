"""Loopback-only, allowlisted static preview. No API or access to repository data."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

class PreviewHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        request = unquote(urlsplit(path).path)
        for prefix, base in (("/prototype/", HERE), ("/console/", ROOT / "console")):
            if request.startswith(prefix):
                candidate = (base / request[len(prefix):]).resolve()
                if candidate.is_relative_to(base.resolve()) and candidate.suffix in {".html", ".css", ".js", ".svg", ".png"}:
                    return str(candidate)
                if candidate == HERE:
                    return str(HERE / "index.html")
        return str(HERE / "__not_served__")

    def do_GET(self):
        if urlsplit(self.path).path == "/":
            self.send_response(302)
            self.send_header("Location", "/prototype/index.html#/sessions/overview")
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8023)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"诊断中心原型: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
