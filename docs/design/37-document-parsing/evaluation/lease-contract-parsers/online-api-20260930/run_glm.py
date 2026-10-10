"""Isolated GLM-OCR evaluation. Never stores or prints credentials."""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import time

import requests

ROOT = Path(__file__).resolve().parent


def run(key: str, source: Path, dest: Path) -> dict:
    if dest.exists():
        raise RuntimeError("Existing result directory; inspect it, do not silently resubmit")
    dest.mkdir(parents=True, mode=0o700)
    raw = source.read_bytes()
    metadata = {"provider": "glm", "model_requested": "glm-ocr", "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw), "status": "started", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    start = time.monotonic()
    payload = {"model": "glm-ocr", "file": "data:application/pdf;base64," + base64.b64encode(raw).decode("ascii")}
    print("GLM request started; full PDF; automatic retries disabled", flush=True)
    try:
        response = requests.post("https://open.bigmodel.cn/api/paas/v4/layout_parsing", headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=(30, 900))
        metadata["response_seconds"] = round(time.monotonic() - start, 3)
        metadata["http_status"] = response.status_code
        body = response.json()
        if not response.ok:
            metadata["error_code"] = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None
            raise RuntimeError(f"HTTP {response.status_code}; error_code={metadata['error_code']}")
        (dest / "response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        md = body.get("md_results", "")
        if isinstance(md, list):
            md = "\n\n".join(str(x) for x in md)
        (dest / "full.md").write_text(md, encoding="utf-8")
        layout = body.get("layout_details", [])
        metadata.update(status="done", model_returned=body.get("model"), usage=body.get("usage"), data_info=body.get("data_info"), response_fields=list(body), layout_top_level_count=len(layout), markdown_bytes=len(md.encode("utf-8")))
    except Exception as exc:
        metadata["status"] = "failed"
        # Exception text can contain upstream URLs or request data. Keep type only.
        metadata["error_type"] = type(exc).__name__
    finally:
        metadata["total_seconds"] = round(time.monotonic() - start, 3)
        (dest / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in dest.iterdir():
            path.chmod(0o600)
    print(json.dumps({k: v for k, v in metadata.items() if k != "data_info"}, ensure_ascii=False), flush=True)
    return metadata


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT.parent / "local-private" / "明日巴适租赁合同+oa.pdf")
    parser.add_argument("--out", type=Path, default=ROOT / "local-private" / "glm")
    args = parser.parse_args()
    token = getpass.getpass("GLM key (hidden, memory only): ")
    if not token:
        raise RuntimeError("Explicit key required")
    run(token, args.source, args.out)
