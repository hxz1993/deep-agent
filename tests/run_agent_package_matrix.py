"""Run the complete independent, isolated 25,600-case installation matrix.

PYTHONPATH=src python3 -m tests.run_agent_package_matrix --workers 4
Use --limit 100 only for a benchmark; limited runs never claim full coverage.
"""
from __future__ import annotations
import argparse
import asyncio
import atexit
import csv
import hashlib
import json
import itertools
import multiprocessing
import platform
import tempfile
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path

_WORKER = {}

def initialize():
    from fastapi import FastAPI
    from loguru import logger
    from deep_agent.api.v1 import api_router
    from tests.helpers.agent_package_matrix import create_schema_template
    logger.disable('deep_agent')
    temp=tempfile.TemporaryDirectory(prefix='TMP_to_delete-agent-package-matrix-')
    atexit.register(temp.cleanup)
    root=Path(temp.name); template=root/'schema.db'
    asyncio.run(create_schema_template(template))
    app=FastAPI(); app.include_router(api_router)
    _WORKER.update(temp=temp,root=root,template=template,app=app)

def execute(case):
    from tests.helpers.agent_package_matrix import matrix_case
    began=time.perf_counter()
    try:
        return asyncio.run(matrix_case(case,_WORKER['root'],_WORKER['template'],_WORKER['app']))
    except Exception as exc:
        return {**case,'status':'failed','checks':0,'duration_ms':round((time.perf_counter()-began)*1000,2),
                'error':str(exc),'traceback':traceback.format_exc()}

def main():
    from tests.helpers.agent_package_matrix import all_cases,DIMENSIONS,TOTAL_CASES
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--report-dir',type=Path,default=Path('docs/reports/agent-package-matrix'))
    args=parser.parse_args()
    cases=list(all_cases()); assert len(cases)==TOTAL_CASES
    if args.limit: cases=cases[:args.limit]
    output=args.report_dir; output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    tracked=sorted([str(p.relative_to(root)) for p in (root/'src/deep_agent').rglob('*.py')]+[str(p.relative_to(root)) for p in (root/'migrations/versions').glob('005*.py')]+['tests/helpers/agent_package_matrix.py','tests/run_agent_package_matrix.py'])
    def fingerprints(): return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in tracked}
    initial_hashes=fingerprints()
    start=datetime.now(timezone.utc).isoformat(); began=time.perf_counter(); results=[]
    stopped=False
    stop_file=output/'STOP'
    if stop_file.exists(): raise SystemExit('Remove report-directory STOP file before starting a new run.')
    last=began
    with (output/'cases.jsonl').open('w') as stream:
        with ProcessPoolExecutor(max_workers=args.workers,initializer=initialize,mp_context=multiprocessing.get_context('spawn')) as pool:
            remaining=iter(cases)
            pending={pool.submit(execute,case) for case in itertools.islice(remaining,args.workers*2)}
            while pending:
                completed,pending=wait(pending,return_when=FIRST_COMPLETED)
                for future in completed:
                    result=future.result(); results.append(result)
                    stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush()
                    now=time.perf_counter()
                    if now-last>=20 or result['status']=='failed' or len(results)==len(cases):
                        counts=Counter(r['status'] for r in results)
                        print(json.dumps({'completed':len(results),'total':len(cases),'passed':counts['passed'],'failed':counts['failed'],'elapsed_s':round(now-began,1),'last':result['case_id']},ensure_ascii=False),flush=True)
                        last=now
                if stop_file.exists(): stopped=True
                if not stopped:
                    pending.update(pool.submit(execute,case) for case in itertools.islice(remaining,len(completed)))
    results.sort(key=lambda x:x['case_id'])
    (output/'cases.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    with (output/'cases.csv').open('w',newline='') as f:
        fields=['case_id',*DIMENSIONS,'status','checks','duration_ms','missing_count','error','expected','actual']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for r in results:
            writer.writerow({**r,**{key:json.dumps(r.get(key,{}),ensure_ascii=False,sort_keys=True) for key in ('expected','actual')}})
    counts=Counter(r['status'] for r in results)
    final_hashes=fingerprints()
    summary={'started_at':start,'finished_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.perf_counter()-began,2),
        'python':platform.python_version(),'platform':platform.platform(),'workers':args.workers,
        'expected_full_cases':TOTAL_CASES,'executed':len(results),'passed':counts['passed'],'failed':counts['failed'],'skipped':0,
        'full_matrix_passed':len(results)==TOTAL_CASES and counts['passed']==TOTAL_CASES and initial_hashes==final_hashes,
        'stopped':stopped,'source_unchanged':initial_hashes==final_hashes,'initial_sha256':initial_hashes,
        'assertions':sum(r['checks'] for r in results),'dimensions':DIMENSIONS,
        'dimension_counts':{key:dict(Counter(r[key] for r in results)) for key in DIMENSIONS},
        'sha256':final_hashes,
        'method':'Independent fixture oracle; per case real SQLite, ZIP, HTTP ASGI endpoints, LocalFileStore, SkillRuntime; preview/install/reread/load/export; no company environment access.'}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if not counts['failed'] and not stopped and initial_hashes==final_hashes else 1

if __name__=='__main__': raise SystemExit(main())
