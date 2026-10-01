"""Replay previously authorized native MinerU ZIPs, without any network call.

This is explicitly historical artifact evidence, not successful execution of
current pending supplier tasks. Existing source hashes must match the manifest.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from deep_agent.documents.models import DocumentSource
from deep_agent.documents.providers import _unpack, _mineru_pages, _result
from run_adapters import ROOT, save


def main():
    os.umask(0o077)
    manifest=json.loads((ROOT/'local-private/manifest.json').read_text())
    artifacts=ROOT.parent/'online-api-20260930/local-private'
    cases={'明日巴适租赁合同+oa.pdf':artifacts/'mineru/result.zip',
           '友阿奥莱租赁合同+oa.pdf':artifacts/'友阿奥莱租赁合同+oa/mineru/result.zip',
           '友阿奥特莱斯补充协议（二）+oa.pdf':artifacts/'友阿奥特莱斯补充协议（二）+oa/mineru/result.zip'}
    for row in manifest:
        if row['name'] not in cases:continue
        historical=json.loads((cases[row['name']].parent/'run.json').read_text())
        assert historical['sample_sha256']==row['sha256'], 'Historical artifact source hash differs'
        raw=Path(row['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        base=ROOT/'local-private/historical-mineru-replay'/row['sha256'][:16]/'mineru'
        if (base/'normalized-result.json').exists():continue
        blob=cases[row['name']].read_bytes();_unpack(blob,base/'artifacts')
        source=DocumentSource(row['name'],row['mime_type'],raw,row['pages'])
        parsed=_result('mineru',source,_mineru_pages(base/'artifacts'),{'historical_artifact_replay':True,'artifact_directory':'artifacts'})
        save(base/'normalized-result.json',asdict(parsed))
        save(base/'evaluation.json',{'evidence':'historical_artifact_replay','remote_request_performed':False,'pages':len(parsed.pages),'source_pages':row['pages'],'status':parsed.status,'source_hash':row['sha256']})
        print('historical replay',row['sha256'][:16],len(parsed.pages),parsed.status)


if __name__=='__main__':main()
