"""Authorized provider artifacts -> production ZIP -> isolated LocalFileStore.

No application/configuration writes. Online Paddle submits one known page once;
replay only downloads URLs already returned in frozen native responses. Outputs
contain private supplier artifacts and must be outside tracked paths. Never
blindly retry a started/failed online request; use explicit new output after review.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, io, json, logging, os, sqlite3, tempfile, time, zipfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import jsonschema
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.documents import providers
from deep_agent.documents.adaptation import adapt_document
from deep_agent.documents.bundle import build_evidence_bundle
from deep_agent.documents.cache import CachedDocument
from deep_agent.documents.evidence import build_document_evidence, SCHEMA_VERSION
from deep_agent.documents.export import DocumentExportManager
from deep_agent.documents.models import DocumentSource
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.documents.toolset import read_cached_document
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.schema import AttachmentFileRef
from deep_agent.files.storage import LocalFileStore
from PIL import Image
EST = TokenEstimator.conservative()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(384)

async def verify(parsed, source, dest, mode):
    started = time.monotonic()
    dest.mkdir(parents=True, exist_ok=True, mode=448)
    save(dest / 'normalized-result.json', asdict(parsed))
    row = {'provider': parsed.provider, 'mode': mode, 'source_pages': source.source_pages, 'status': 'started'}
    try:
        sha = hashlib.sha256(source.data).hexdigest()
        docid = 'doc_zip_' + sha[:16] + '_' + parsed.provider
        readable = await asyncio.to_thread(adapt_document, parsed, EST)
        evidence = await asyncio.to_thread(build_document_evidence, parsed, readable, docid, EST)
        record = CachedDocument(docid, AttachmentFileRef(type='attachment', file_id='local_isolated_authorized_source'), 'attachment:local_isolated_authorized_source', sha, parsed, readable)
        whole = read_cached_document(record, budget=ReadingBudget(EST, 256000), evidence=evidence)
        payload = evidence.export_dict(record.resource.model_dump(mode='json'), sha, whole.summary.model_dump(mode='json'), source_storage='local')
        calls = 0

        async def make():
            nonlocal calls
            calls += 1
            return await build_evidence_bundle(payload, parsed)
        manager = DocumentExportManager()
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete_bundle_store_') as root:
            store = LocalFileStore(root)
            await store.initialize()
            headers = {'user': 'isolated-evidence-bundle-validation'}
            try:
                a, b = await asyncio.gather(*(manager.ensure(docid, make, file_store=store, request_headers=headers, schema_version=SCHEMA_VERSION) for _ in range(2)))
                assert a.status == 'saved', a.error.code if a.error else a.status
                assert a == b and calls == 1
                data = await store.download(a.file.file_id, headers)
                assert hashlib.sha256(data).hexdigest() == a.file.sha256
                (dest / 'evidence.zip').write_bytes(data)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    body = json.loads(z.read('evidence.json'))
                    jsonschema.validate(body, json.loads(Path('docs/design/37-document-parsing/document-evidence.schema.json').read_text()))
                    assert len(body['pages']) == len(payload['pages'])
                    for actual, expected in zip(body['pages'], payload['pages'], strict=True):
                        assert {k: v for k, v in actual.items() if k != 'image'} == expected
                        im = actual.get('image', {})
                        if im.get('status') == 'saved':
                            image = z.read(im['path'])
                            assert hashlib.sha256(image).hexdigest() == im['sha256']
                            with Image.open(io.BytesIO(image)) as i:
                                assert list(i.size) == [im['width'], im['height']]
                pieces = [[] for _ in evidence.pages]
                params = {'include_layout': True}
                reads = 0
                while True:
                    result = read_cached_document(record, budget=ReadingBudget(EST, 256000), evidence=evidence, **params)
                    reads += 1
                    assert ReadingBudget(EST, 256000).fits(result.model_dump(mode='json'))
                    for i, p in enumerate(result.pages, result.offset - 1):
                        pieces[i].append((p.char_offset, p.char_end, p.content))
                    again = await manager.ensure(docid, make, file_store=store, request_headers=headers, schema_version=SCHEMA_VERSION)
                    assert again == a and calls == 1
                    if not result.has_more:
                        break
                    params = {k: v for k, v in result.next_read.items() if k != 'resource'}
                for page, parts in zip(evidence.pages, pieces, strict=True):
                    cursor = 0
                    for start, end, text in parts:
                        assert start == cursor and text == page.content[start:end]
                        cursor = end
                    assert cursor == len(page.content)
                save(dest / 'evidence.json', body)
                row.update(status='passed', zip_bytes=len(data), sha256=a.file.sha256, images=body['images'], image_statuses=dict(Counter((p['image']['status'] for p in body['pages']))), image_reasons=dict(Counter((p['image'].get('reason_code', 'none') for p in body['pages']))), coordinate_mappings=dict(Counter((p['image']['coordinate_mapping'] for p in body['pages']))), blocks=sum((len(p['blocks']) for p in body['pages'])), read_calls_48k_layout=reads, body_and_block_ids_unchanged=True, local_roundtrip_hash_schema=True, export_build_calls=calls)
            finally:
                await manager.aclose()
    except Exception as e:
        row.update(status='failed', error_type=type(e).__name__)
        if isinstance(e, AssertionError):
            row['assertion'] = str(e)
    row['seconds'] = round(time.monotonic() - started, 3)
    save(dest / 'evaluation.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row

async def online(args):
    from deep_agent.api.v1.document_parsing import connection_sample
    dest = args.output / 'online-small'
    target = dest / 'paddle'
    if (target / 'attempt.json').exists():
        raise RuntimeError('Existing online attempt; no automatic resubmission')
    source = connection_sample()
    dest.mkdir(parents=True, exist_ok=True, mode=448)
    (dest / 'known-one-page.pdf').write_bytes(source.data)
    with sqlite3.connect(f'file:{args.local_db.resolve()}?mode=ro', uri=True) as c:
        b = json.loads(c.execute('SELECT value FROM global_settings WHERE key=?', ('document_parsing',)).fetchone()[0])
    assert b.get('provider') == 'paddle' and b.get('api_key')
    save(target / 'attempt.json', {'status': 'started', 'provider': 'paddle', 'mode': 'current-online', 'sha256': hashlib.sha256(source.data).hexdigest()})
    try:
        parsed = await providers.parse_document(DocumentParsingRuntimeConfig('provider', 'paddle', b['api_key']), source, target)
        save(target / 'attempt.json', {'status': 'done', 'parsed_pages': len(parsed.pages)})
        return await verify(parsed, source, target, 'current-online')
    except Exception as e:
        save(target / 'attempt.json', {'status': 'failed', 'type': type(e).__name__, 'code': getattr(e, 'code', None)})
        raise

async def replay(args):
    rows = json.loads((args.fixtures / 'manifest.json').read_text())
    results = []
    for row in rows:
        if row['mime_type'] != 'application/pdf':
            continue
        raw = Path(row['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        source = DocumentSource(row['name'], row['mime_type'], raw, row['pages'])
        for provider in ('paddle', 'glm'):
            src = args.fixtures / 'production-initial' / row['sha256'][:16] / provider
            if provider == 'paddle':
                pages = providers._paddle_pages((src / 'result.jsonl').read_bytes())
                native = {}
            else:
                native = json.loads((src / 'response.json').read_text())
                pages = providers._glm_pages(native)
            parsed = providers._result(provider, source, pages, native)
            result = await verify(parsed, source, args.output / 'historical' / row['sha256'][:16] / provider, 'historical-native-replay-real-image-download')
            results.append(result)
            save(args.output / 'historical-summary.json', results)
    return results

async def glm_sample(args):
    source_path = args.glm_source
    source = DocumentSource(source_path.name, 'application/pdf', source_path.read_bytes(), 1)
    native = json.loads(args.glm_response.read_text())
    parsed = providers._result('glm', source, providers._glm_pages(native), native)
    return await verify(parsed, source, args.output / 'online-small' / 'glm', 'prior-online-20261011-native-replay-real-image-download')

async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['online-paddle', 'replay', 'glm-sample'], required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixtures', type=Path)
    p.add_argument('--local-db', type=Path, default=Path('data/deep-agent-local.db'))
    p.add_argument('--glm-response', type=Path)
    p.add_argument('--glm-source', type=Path)
    a = p.parse_args()
    os.umask(63)
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    if a.mode == 'online-paddle':
        r = await online(a)
    elif a.mode == 'glm-sample':
        r = await glm_sample(a)
    else:
        r = await replay(a)
    if any((x['status'] != 'passed' for x in (r if isinstance(r, list) else [r]))):
        raise SystemExit('Validation failed; inspect safe private summary')
if __name__ == '__main__':
    asyncio.run(main())
