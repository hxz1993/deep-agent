"""Authorized one-page OCR probes; keys only in memory, outputs private.

Run from repository root with PYTHONPATH=src. The GLM key is requested with
getpass. Paddle uses the authorized current local setting without changing it.
Each provider submits once; a confirmed existing result is reused. Retrying a
failed task requires an explicit new output folder, not an automatic re-POST.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


async def run(provider: str, key: str, output: Path) -> dict:
    from deep_agent.api.v1.document_parsing import connection_sample
    from deep_agent.documents.models import DocumentParsingError
    from deep_agent.documents.providers import parse_document
    from deep_agent.documents.settings import DocumentParsingRuntimeConfig
    source = connection_sample()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = output / provider
    if (target / "evaluation.json").exists():
        return json.loads((target / "evaluation.json").read_text())
    source_path = output / "known-one-page.pdf"
    source_path.write_bytes(source.data)
    source_path.chmod(0o600)
    started = time.monotonic()
    row = {"provider": provider, "mode": "online", "source_sha256": hashlib.sha256(source.data).hexdigest(), "source_pages": 1,
           "expected_strings": ["DeepAgent Document Test", "123.45"], "status": "started"}
    save(target / "evaluation.json", row)
    try:
        parsed = await parse_document(DocumentParsingRuntimeConfig("provider", provider, key), source, target,
                                      context={"session_id": "evidence-validation-isolated", "run_id": "known-one-page", "tool_name": "parse_document"})
        save(target / "normalized-result.json", asdict(parsed))
        joined = "\n".join(page.content for page in parsed.pages)
        row.update(status="done", model=parsed.model, parsed_pages=len(parsed.pages), parse_status=parsed.status,
                   known_answer_matches={value: value in joined for value in row["expected_strings"]})
    except DocumentParsingError as error:
        row.update(status="failed", code=error.code)
    except Exception as error:
        row.update(status="failed", exception_type=type(error).__name__)
    row["seconds"] = round(time.monotonic() - started, 3)
    save(target / "evaluation.json", row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-db", type=Path, default=Path("data/deep-agent-local.db"))
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    with sqlite3.connect(f"file:{args.local_db.resolve()}?mode=ro", uri=True) as conn:
        stored = json.loads(conn.execute("SELECT value FROM global_settings WHERE key=?", ("document_parsing",)).fetchone()[0])
    if stored.get("provider") != "paddle" or not stored.get("api_key"):
        raise RuntimeError("Local active Paddle config required; no mutation performed")
    glm = getpass.getpass("GLM key (hidden, memory only): ")
    if not glm:
        raise RuntimeError("GLM key required")
    rows = await asyncio.gather(run("paddle", stored["api_key"], args.output), run("glm", glm, args.output))
    save(args.output / "summary.json", rows)
    if any(row.get("status") != "done" or not all(row.get("known_answer_matches", {}).values()) for row in rows):
        raise SystemExit("One or more OCR probes did not pass; inspect private safe summary")

if __name__ == "__main__":
    asyncio.run(main())
