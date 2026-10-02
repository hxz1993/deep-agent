"""Run selected real PDF samples once per provider, isolated from product."""
import argparse
import concurrent.futures
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path

from pypdf import PdfReader

import run_glm

ROOT = Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", nargs="+", type=Path)
    parser.add_argument("--trial", default="initial", help="Explicit trial name; each directory is submitted at most once")
    args = parser.parse_args()
    if not args.trial or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.trial):
        raise RuntimeError("Trial name must use lowercase ASCII letters, digits, hyphens or underscores")
    output_root = ROOT / "local-private" if args.trial == "initial" else ROOT / "local-private" / args.trial
    for sample in args.samples:
        if (output_root / sample.stem).exists():
            raise RuntimeError("Existing case output; inspect it rather than resubmit")
    keys = {p: getpass.getpass(f"{p} key (hidden, memory only): ") for p in ("mineru", "paddle", "glm")}
    if not all(keys.values()):
        raise RuntimeError("Three explicit credentials required")
    for sample in args.samples:
        spec = importlib.util.spec_from_file_location("isolated_case", ROOT / "run_online.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SAMPLE = sample.resolve()
        module.EXPECTED_HASH = hashlib.sha256(sample.read_bytes()).hexdigest()
        module.EXPECTED_PAGES = len(PdfReader(sample).pages)
        module.POLL_SECONDS = 5
        module.OUT = output_root / sample.stem
        module.SECRETS.extend(keys.values())
        print(f"Case: {sample.stem}; pages={module.EXPECTED_PAGES}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(module.execute, p, keys[p], fn) for p, fn in (("mineru", module.mineru), ("paddle", module.paddle))]
            futures.append(executor.submit(run_glm.run, keys["glm"], sample, module.OUT / "glm"))
            results = [f.result() for f in futures]
        module.save(module.OUT / "summary.json", results)
        print(json.dumps([{k: r.get(k) for k in ("provider", "status", "expected_pages", "total_seconds", "error", "usage")} for r in results], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
