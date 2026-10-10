"""Offline production reader reconstruction: no external model/parser requests."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from deep_agent.documents.cache import CachedDocument
from deep_agent.documents.adaptation import adapt_document
from deep_agent.documents.models import ParsedDocument, DocumentPage
from deep_agent.documents.toolset import read_cached_document
from deep_agent.files.schema import AttachmentFileRef
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.agent.context.estimator import TokenEstimator
from pydantic_ai.messages import ToolReturnPart
from run_adapters import ROOT, save


def reconstruct(path:Path,window:int)->dict:
    raw=json.loads(path.read_text())
    raw['pages']=[DocumentPage(**p) for p in raw['pages']]
    parsed=ParsedDocument(**raw)
    readable=adapt_document(parsed,TokenEstimator.conservative())
    record=CachedDocument('doc_test',AttachmentFileRef(type='attachment',file_id='authorized_test'), 'attachment:authorized_test','test_hash',parsed,readable)
    budget=ReadingBudget(TokenEstimator.conservative(),window)
    params={}; fragments={i:[] for i in range(len(parsed.pages))}; stats=[]; seen=set()
    while True:
        result=read_cached_document(record,budget=budget,**params)
        wire=result.model_dump(mode='json')
        assert budget.fits(wire),'Tool envelope exceeds token budget'
        current=result.offset-1
        for j,p in enumerate(result.pages):
            expected=readable.pages[current+j]
            assert p.content==expected.content[p.char_offset:p.char_end], 'Fragment altered model-readable body'
            fragments[current+j].append((p.char_offset,p.char_end,p.content))
        stats.append({'returned_pages':result.returned_count,'token_count':budget.estimator.text(wire),
                      'wire_tokens':budget.estimator.text(ToolReturnPart('parse_document',wire).model_response_str()),
                      'offset':result.offset,'char_offset':result.char_offset,'has_more':result.has_more})
        if not result.has_more:break
        args=dict(result.next_read);args.pop('resource')
        position=(args['offset'],args['char_offset'])
        assert position not in seen,'Continuation loop'
        seen.add(position);params=args
    for i,p in enumerate(readable.pages):
        pieces=fragments[i];cursor=0
        for start,end,_ in pieces:
            assert start==cursor,'Missing/duplicate character range'
            cursor=end
        assert cursor==len(p.content)
        assert ''.join(x[2] for x in pieces)==p.content,'Model-readable body was lost or rewritten'
    return {'provider':parsed.provider,'window':window,'budget':budget.tokens,'calls':len(stats),
            'pages':len(parsed.pages),'native_characters':sum(len(p.content) for p in parsed.pages),
            'content_characters':sum(len(p.content) for p in readable.pages),
            'content_preserved':True,'max_tokens':max(x['token_count'] for x in stats),'batches':stats}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--trial',default='production-initial');args=parser.parse_args()
    output=ROOT/'local-private'/args.trial
    results=[]
    for path in sorted(output.glob('*/*/normalized-result.json')):
        for window in (128_000,1_000_000):
            row=reconstruct(path,window);row['case_hash']=path.parents[1].name;results.append(row)
            print({k:row[k] for k in ('provider','case_hash','budget','calls','pages','content_preserved','max_tokens')})
    save(output/'paging-summary.json',results)


if __name__=='__main__':main()
