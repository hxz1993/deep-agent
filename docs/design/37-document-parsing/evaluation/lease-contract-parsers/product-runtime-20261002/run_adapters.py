"""One authorized submission per sample/provider via production adapters.

Results and source metadata stay under Git-ignored local-private. Credentials
come from hidden terminal input and live only in process memory. Existing task
folders resume the SAME remote task; unknown submission states are not retried.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import time

from deep_agent.documents.models import DocumentSource, DocumentParsingError
from deep_agent.documents.providers import parse_document
from deep_agent.documents.settings import DocumentParsingRuntimeConfig

ROOT = Path(__file__).resolve().parent


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    path.chmod(0o600)


async def run_case(row: dict, provider: str, key: str, output: Path) -> dict:
    cache = output / row['sha256'][:16] / provider
    result_path = cache / 'normalized-result.json'
    metadata_path = cache / 'evaluation.json'
    if result_path.exists():
        print(f"{provider} {row['name']}: local complete result reused; no request", flush=True)
        return json.loads(metadata_path.read_text())
    raw = Path(row['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise RuntimeError('Source changed after source-gold inventory')
    start = time.monotonic()
    metadata = {k: row[k] for k in ('name', 'sha256', 'bytes', 'pages', 'mime_type')}
    metadata.update(provider=provider, started_at=datetime.now().astimezone().isoformat(),
                    status='started', resumed_same_task=(cache / 'task.json').exists())
    if metadata_path.exists():
        previous=json.loads(metadata_path.read_text())
        history=cache/'attempts.json'
        attempts=json.loads(history.read_text()) if history.exists() else []
        if previous not in attempts:attempts.append(previous)
        save(history,attempts)
    save(metadata_path, metadata)
    print(f"{provider} {row['name']}: production adapter started", flush=True)
    try:
        source = DocumentSource(row['name'], row['mime_type'], raw, row['pages'])
        result = await parse_document(DocumentParsingRuntimeConfig('provider', provider, key), source, cache,
                                      context={'document_evaluation': True, 'case_hash': row['sha256'][:16]})
        save(result_path, asdict(result))
        metadata.update(status='done', parse_status=result.status, returned_pages=len(result.pages),
                        warnings=result.warnings, native_characters=sum(len(x.content) for x in result.pages),
                        model=result.model, usage=result.native.get('usage'))
    except DocumentParsingError as exc:
        metadata.update(status='failed', error_code=exc.code, error_message=str(exc))
    except asyncio.CancelledError:
        metadata.update(status='cancelled')
        raise
    except Exception as exc:
        # Upstream exceptions can include URL/query/body; never print them.
        metadata.update(status='failed', error_type=type(exc).__name__)
    finally:
        metadata['seconds'] = round(time.monotonic()-start, 3)
        save(metadata_path, metadata)
    print(json.dumps({k: metadata.get(k) for k in ('provider','name','status','error_code','seconds','returned_pages')}, ensure_ascii=False), flush=True)
    return metadata


async def execute(args, keys: dict[str,str]) -> None:
    rows = json.loads((ROOT/'local-private'/'manifest.json').read_text())
    if args.samples:
        rows = [x for x in rows if x['name'] in args.samples]
    if not rows:
        raise RuntimeError('No known samples selected')
    output = ROOT/'local-private'/args.trial
    results = []
    semaphore = asyncio.Semaphore(args.concurrency)
    async def run_row(row):
        async with semaphore:
            applicable = [p for p in args.providers if (row['mime_type']=='application/pdf' or row['mime_type'].startswith('image/')) or p=='mineru']
            results.extend(await asyncio.gather(*(run_case(row,p,keys[p],output) for p in applicable)))
            save(output/'summary.json', results)
    await asyncio.gather(*(run_row(row) for row in rows))


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--providers', nargs='+', choices=('mineru','paddle','glm'), default=['mineru','paddle','glm'])
    parser.add_argument('--samples', nargs='*')
    parser.add_argument('--trial', default='production-initial')
    parser.add_argument('--concurrency', type=int, default=1)
    args=parser.parse_args()
    if args.concurrency<1:parser.error('--concurrency must be positive')
    if not args.trial or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.trial):
        parser.error('Trial must use lowercase letters, digits, - or _')
    keys={p:getpass.getpass(f'{p} key (hidden, memory only): ') for p in args.providers}
    if not all(keys.values()):raise RuntimeError('All selected credentials are required')
    asyncio.run(execute(args,keys))


if __name__=='__main__':main()
