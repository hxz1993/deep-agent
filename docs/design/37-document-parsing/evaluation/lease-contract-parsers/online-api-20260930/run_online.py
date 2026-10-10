"""Isolated full-contract API experiment; credentials stay in memory."""
from __future__ import annotations

import concurrent.futures
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

import requests

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT.parent / "local-private" / "明日巴适租赁合同+oa.pdf"
EXPECTED_HASH = "eed541ff251bff4d4f699b120fe6f5e32fd8b26fbd3d9f5d6099a9b77c74fe3d"
EXPECTED_PAGES = 31
POLL_SECONDS = 10
OUT = ROOT / "local-private"
SECRETS: list[str] = []


def clean(value: object) -> str:
    text = str(value)
    for secret in SECRETS:
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"https?://\S+", "[URL REDACTED]", text)[:350]


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def response_data(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError(f"HTTP {response.status_code}, non-JSON response") from None
    if not response.ok or body.get("code", 0) not in (0, None):
        message = body.get("msg") or body.get("message") or body.get("errorMsg") or body.get("error")
        raise RuntimeError(clean(f"HTTP {response.status_code}; code={body.get('code')}; {message}"))
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response shape; fields={list(body)}")
    return data


def record(result: dict, phase: str, **fields: object) -> None:
    event = {"phase": phase, "elapsed_seconds": round(time.monotonic() - result["_start"], 2), **fields}
    result["events"].append(event)
    save(OUT / result["provider"] / "run.json", {k: v for k, v in result.items() if k != "_start"})
    print(f"{result['provider']}: {phase} {clean(fields)}", flush=True)


def download(url: str) -> bytes:
    # No provider bearer header is forwarded to signed storage URLs.
    try:
        with requests.get(url, timeout=(20, 180)) as response:
            if not response.ok:
                raise RuntimeError(f"Result download HTTP {response.status_code}")
            return response.content
    except requests.exceptions.SSLError:
        # Resume artifact download only, using system TLS validation. No -k,
        # no job resubmission, no provider credentials in storage requests.
        process = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "180", "--config", "-"], input=f'url = "{url}"\n'.encode(), capture_output=True)
        if process.returncode:
            raise RuntimeError("System TLS artifact download failed") from None
        return process.stdout


def mineru(key: str, result: dict) -> None:
    result["requested_model"] = "vlm"
    result["actual_checkpoint"] = "not disclosed by hosted API"
    with requests.Session() as session:
        session.headers["Authorization"] = f"Bearer {key}"
        payload = {"files": [{"name": "lease-contract.pdf", "data_id": EXPECTED_HASH[:24]}], "model_version": "vlm", "enable_table": True, "enable_formula": True, "language": "ch"}
        record(result, "request_upload")
        data = response_data(session.post("https://mineru.net/api/v4/file-urls/batch", json=payload, timeout=(20, 90)))
        save(OUT / "mineru" / "upload-response.json", data)
        batch = data["batch_id"]
        result["job_id"] = batch
        record(result, "upload_url_received")
        with SAMPLE.open("rb") as source:
            with requests.put(data["file_urls"][0], data=source, timeout=(20, 180)) as response:
                if not response.ok:
                    raise RuntimeError(f"Upload HTTP {response.status_code}")
        record(result, "uploaded")
        deadline = time.monotonic() + 900
        last = None
        while time.monotonic() < deadline:
            data = response_data(session.get(f"https://mineru.net/api/v4/extract-results/batch/{batch}", timeout=(20, 90)))
            save(OUT / "mineru" / "status.json", data)
            item = data["extract_result"][0]
            state = item["state"]
            progress = item.get("extract_progress", {})
            signature = (state, progress.get("extracted_pages"))
            if signature != last:
                record(result, state, progress=progress)
                last = signature
            if state == "failed":
                raise RuntimeError(clean(item.get("err_msg", "remote task failed")))
            if state == "done":
                blob = download(item["full_zip_url"])
                archive_path = OUT / "mineru" / "result.zip"
                archive_path.write_bytes(blob)
                archive_path.chmod(0o600)
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    if sum(entry.file_size for entry in archive.infolist()) > 200_000_000:
                        raise RuntimeError("Result archive exceeds experiment limit")
                    dest = (OUT / "mineru" / "artifacts").resolve()
                    for entry in archive.infolist():
                        target = (dest / entry.filename).resolve()
                        if not target.is_relative_to(dest):
                            raise RuntimeError("Unsafe archive member path")
                    archive.extractall(dest)
                record(result, "downloaded", zip_bytes=len(blob))
                result["status"] = "done"
                return
            time.sleep(POLL_SECONDS)
        raise TimeoutError("900s poll timeout; remote task may still be running; no resubmission")


def paddle(key: str, result: dict) -> None:
    result["requested_model"] = "PaddleOCR-VL-1.6"
    result["options"] = {"mergeTables": True, "relevelTitles": True}
    url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    with requests.Session() as session:
        session.headers["Authorization"] = f"Bearer {key}"
        record(result, "submit_upload")
        with SAMPLE.open("rb") as source:
            data = response_data(session.post(url, data={"model": result["requested_model"], "optionalPayload": json.dumps(result["options"])}, files={"file": ("lease-contract.pdf", source, "application/pdf")}, timeout=(20, 180)))
        save(OUT / "paddle" / "submission.json", data)
        job = data["jobId"]
        result["job_id"] = job
        record(result, "uploaded_and_submitted")
        deadline = time.monotonic() + 900
        last = None
        while time.monotonic() < deadline:
            data = response_data(session.get(f"{url}/{job}", timeout=(20, 90)))
            save(OUT / "paddle" / "status.json", data)
            state = data["state"]
            if state != last:
                record(result, state)
                last = state
            if state == "failed":
                raise RuntimeError(clean(data.get("errorMsg", "remote task failed")))
            if state == "done":
                blob = download(data["resultUrl"]["jsonUrl"])
                raw_path = OUT / "paddle" / "result.jsonl"
                raw_path.write_bytes(blob)
                raw_path.chmod(0o600)
                pages = []
                for line in blob.decode("utf-8").splitlines():
                    if line.strip():
                        obj = json.loads(line)
                        pages.extend(obj["result"]["layoutParsingResults"])
                markdown = []
                for index, page in enumerate(pages, 1):
                    text = page["markdown"]["text"]
                    path = OUT / "paddle" / "pages" / f"page-{index:03}.md"
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_text(text, encoding="utf-8")
                    path.chmod(0o600)
                    markdown.append(f"<!-- physical page candidate {index}; verify page_index metadata -->\n\n{text}")
                full = OUT / "paddle" / "full.md"
                full.write_text("\n\n".join(markdown), encoding="utf-8")
                full.chmod(0o600)
                result["returned_page_count"] = len(pages)
                result["status"] = "done"
                record(result, "downloaded", returned_page_count=len(pages), result_bytes=len(blob))
                return
            time.sleep(POLL_SECONDS)
        raise TimeoutError("900s poll timeout; remote task may still be running; no resubmission")


def execute(provider: str, key: str, function) -> dict:
    result = {"provider": provider, "sample_sha256": EXPECTED_HASH, "expected_pages": EXPECTED_PAGES, "scope": "full_document", "status": "running", "events": [], "_start": time.monotonic()}
    try:
        function(key, result)
    except Exception as error:
        result["status"] = "failed"
        result["error"] = clean(f"{type(error).__name__}: {error}")
        record(result, "failed", error=result["error"])
    finally:
        result["total_seconds"] = round(time.monotonic() - result.pop("_start"), 2)
        save(OUT / provider / "run.json", result)
    return result


def main() -> None:
    os.umask(0o077)
    if hashlib.sha256(SAMPLE.read_bytes()).hexdigest() != EXPECTED_HASH:
        raise RuntimeError("Sample hash mismatch")
    if any((OUT / p / "run.json").exists() for p in ("mineru", "paddle")):
        raise RuntimeError("Existing run found; inspect/resume it rather than submit duplicate tasks")
    keys = {"mineru": getpass.getpass("MinerU token (hidden): "), "paddle": getpass.getpass("Paddle AI Studio token (hidden): ")}
    SECRETS.extend(keys.values())
    if not all(keys.values()):
        raise RuntimeError("Both explicit credentials are required")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute, p, keys[p], fn) for p, fn in (("mineru", mineru), ("paddle", paddle))]
        results = [f.result() for f in futures]
    save(OUT / "summary.json", results)
    print(json.dumps([{k: r.get(k) for k in ("provider", "status", "total_seconds", "returned_page_count", "error")} for r in results], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
