"""Preview only this prototype and an explicit allowlist of existing Console assets."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parent
CONSOLE = ROOT.parents[3] / 'console'
ASSETS = {name:CONSOLE/path for name,path in {
    'themes.css':'themes.css','console.css':'console.css','ui-primitives.css':'ui-primitives.css',
    'favicon.svg':'favicon.svg','lucide.min.js':'vendor/lucide.min.js',
    'marked.min.js':'vendor/marked.min.js','purify.min.js':'vendor/purify.min.js',
}.items()}
class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean=urlsplit(path).path
        if clean.startswith('/assets/'):
            return str(ASSETS.get(clean.removeprefix('/assets/'),ROOT/'__missing__'))
        return super().translate_path(path)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'")
        super().end_headers()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8024);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(ROOT)))
    print(f'Skill prototype: http://127.0.0.1:{args.port}/',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
