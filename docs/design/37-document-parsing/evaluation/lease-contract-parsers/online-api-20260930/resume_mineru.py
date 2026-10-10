"""Recover an existing MinerU job; never submit another parsing task."""
import argparse
import getpass
import io
import json
import os
from pathlib import Path
import time
import zipfile

import requests

import run_online


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    dest = args.case_dir.resolve() / "mineru"
    original = json.loads((dest / "run.json").read_text())
    batch = original["job_id"]
    key = getpass.getpass("MinerU key (hidden, memory only): ")
    run_online.SECRETS.append(key)
    started = time.monotonic()
    events = []
    with requests.Session() as session:
        session.headers["Authorization"] = f"Bearer {key}"
        while time.monotonic() - started < 900:
            response = session.get(f"https://mineru.net/api/v4/extract-results/batch/{batch}", timeout=(20, 90))
            if response.status_code == 429:
                # Polling only. Honour a numeric Retry-After, otherwise back off.
                delay = response.headers.get("Retry-After", "30")
                time.sleep(min(120, max(30, float(delay) if delay.isdigit() else 30)))
                continue
            data = run_online.response_data(response)
            run_online.save(dest / "recovered-status.json", data)
            item = data["extract_result"][0]
            state = item["state"]
            events.append({"state": state, "elapsed_seconds": round(time.monotonic() - started, 2)})
            print(f"Existing job: {state}", flush=True)
            if state == "failed":
                raise RuntimeError(run_online.clean(item.get("err_msg", "Remote task failed")))
            if state == "done":
                blob = run_online.download(item["full_zip_url"])
                (dest / "result.zip").write_bytes(blob)
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    if sum(entry.file_size for entry in archive.infolist()) > 200_000_000:
                        raise RuntimeError("Result archive exceeds experiment limit")
                    artifacts = (dest / "artifacts").resolve()
                    for entry in archive.infolist():
                        if not (artifacts / entry.filename).resolve().is_relative_to(artifacts):
                            raise RuntimeError("Unsafe archive member path")
                    archive.extractall(artifacts)
                run_online.save(dest / "recovery.json", {
                    "status": "done", "job_id": batch, "events": events,
                    "recovery_seconds": round(time.monotonic() - started, 2),
                    "zip_bytes": len(blob), "resubmitted": False,
                    "original_end_to_end_timing_valid": False,
                })
                print("Existing result recovered; original end-to-end timing remains invalid.", flush=True)
                return
            time.sleep(10)
    raise TimeoutError("Existing job did not finish within recovery window")


if __name__ == "__main__":
    main()
