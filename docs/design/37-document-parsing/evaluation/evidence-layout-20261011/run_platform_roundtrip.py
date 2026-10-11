"""One isolated synthetic source/evidence round trip on the trusted FileStore.

Loads the explicitly selected existing local profile in this subprocess only.
No database connection, configuration writes or download-center registration.
New evidence upload uses the production single-attempt route. Confirmed files
are deleted in finally; unknown uploads cannot be safely located or retried.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import io
import zipfile
import json
import logging
import os
from pathlib import Path
import time


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--online", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    if args.output.exists():
        raise SystemExit("Use a new explicit output file; existing attempt is never silently retried")
    from deep_agent.local_dev import load_local_config
    identity = load_local_config(args.profile)
    from deep_agent.agent.context.estimator import TokenEstimator
    from deep_agent.documents.adaptation import adapt_document
    from deep_agent.documents.cache import CachedDocument
    from deep_agent.documents.evidence import build_document_evidence, SCHEMA_VERSION
    from deep_agent.documents.bundle import build_evidence_bundle
    from deep_agent.documents.export import DocumentExportManager
    from deep_agent.documents.models import DocumentPage, ParsedDocument
    from deep_agent.documents.toolset import read_cached_document
    from deep_agent.files.reading_budget import ReadingBudget
    from deep_agent.files.schema import AttachmentFileRef
    from deep_agent.files.storage import PlatformFileStore
    from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
    headers = {key.decode(): value.decode() for key, value in identity.request_headers()}
    store = PlatformFileStore()
    manager = DocumentExportManager()
    confirmed = []
    record = {"mode": "actual-platform-file-roundtrip", "status": "started", "evidence_uploads": 0}
    save(args.output, record)
    started = time.monotonic()
    try:
        source = (args.online / "known-one-page.pdf").read_bytes()
        uploaded = await asyncio.wait_for(store.upload_once(file_type=FILE_TYPE_ATTACHMENT,
            file_name="TMP_to_delete_document_evidence_source.pdf", mime="application/pdf", data=source, request_headers=headers), 45)
        confirmed.append(uploaded.id)
        fetched = await asyncio.wait_for(store.download_bounded(uploaded.id, headers, max_bytes=len(source)), 45)
        assert fetched == source, "Source PDF failed round trip"
        data = json.loads((args.online / "paddle" / "normalized-result.json").read_text())
        data["pages"] = [DocumentPage(**p) for p in data["pages"]]
        parsed = ParsedDocument(**data)
        estimator = TokenEstimator.conservative()
        readable = adapt_document(parsed, estimator)
        cache = CachedDocument("doc_platform_known_sample", AttachmentFileRef(type="attachment", file_id=uploaded.id),
                               "attachment:" + uploaded.id, hashlib.sha256(source).hexdigest(), parsed, readable)
        evidence = build_document_evidence(parsed, readable, cache.document_id, estimator)
        page = read_cached_document(cache, budget=ReadingBudget(estimator, 256000))
        body = evidence.export_dict(cache.resource.model_dump(mode="json"), cache.source_hash,
                                    page.summary.model_dump(mode="json"), source_storage="platform")
        payload = await build_evidence_bundle(body, parsed)
        original_upload = store.upload_once
        async def counted_upload(**kwargs):
            record["evidence_uploads"] += 1
            return await original_upload(**kwargs)
        store.upload_once = counted_upload
        results = await asyncio.wait_for(asyncio.gather(*[
            manager.ensure(cache.document_id, payload, file_store=store, request_headers=headers, schema_version=SCHEMA_VERSION)
            for _ in range(3)]), 60)
        result = results[0]
        record["export_status"] = result.status
        if result.file:
            confirmed.append(result.file.file_id)
        assert result.status == "saved", "Evidence upload did not confirm saved"
        assert all(other == result for other in results)
        repeated = await manager.ensure(cache.document_id, payload, file_store=store, request_headers=headers, schema_version=SCHEMA_VERSION)
        assert repeated == result and record["evidence_uploads"] == 1
        downloaded = await asyncio.wait_for(store.download_bounded(result.file.file_id, headers, max_bytes=len(payload)), 45)
        assert downloaded == payload
        assert hashlib.sha256(downloaded).hexdigest() == result.file.sha256
        assert json.loads(zipfile.ZipFile(io.BytesIO(downloaded)).read("evidence.json"))["schema_version"] == SCHEMA_VERSION
        record.update(status="passed", source_hash_matches=True, export_hash_matches=True, schema_matches=True,
                      concurrent_reuse=True, repeated_reuse=True, evidence_file=result.file.model_dump(mode="json"))
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            record["assertion"] = str(exc)
    finally:
        await manager.aclose()
        cleanup = []
        for file_id in reversed(confirmed):
            try:
                await asyncio.wait_for(store.delete(file_id, headers), 30)
                cleanup.append({"file_id": file_id, "status": "deleted"})
            except Exception as exc:
                cleanup.append({"file_id": file_id, "status": "delete_failed", "error_type": type(exc).__name__})
        record["cleanup"] = cleanup
        record["seconds"] = round(time.monotonic() - started, 3)
        save(args.output, record)
        print(json.dumps({key: value for key, value in record.items() if key not in ("evidence_file", "cleanup")}, ensure_ascii=False))
    if record["status"] != "passed" or any(c["status"] != "deleted" for c in record["cleanup"]):
        raise SystemExit("Platform round trip incomplete; inspect private safe summary")

if __name__ == "__main__":
    asyncio.run(main())
