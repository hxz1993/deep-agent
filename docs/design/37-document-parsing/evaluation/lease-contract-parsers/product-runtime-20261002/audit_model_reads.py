"""Check actual model reads cover every native character, not just last page.

Checks original ToolReturnPart payloads against normalized source. No supplier or
LLM requests are performed; overlapping reads cannot count as new coverage.
"""
from collections import defaultdict
import json
from pathlib import Path
from deep_agent.documents.cache import CachedDocument
from deep_agent.documents.models import DocumentPage, ParsedDocument
from deep_agent.files.schema import AttachmentFileRef
from run_adapters import ROOT, save


def load_record(path:Path,document_id:str):
    body=json.loads(path.read_text());body['pages']=[DocumentPage(**p) for p in body['pages']]
    file_id='supplement_file' if document_id=='doc_supplement' else 'test_file'
    return CachedDocument(document_id,AttachmentFileRef(type='attachment',file_id=file_id),'attachment:'+file_id,'protected_source',ParsedDocument(**body))


def main():
    summaries=[]
    for trial in ('production-initial','historical-mineru-replay','timing-independent'):
        base=ROOT/'local-private'/trial
        for mode in ('mapping','paired-mapping'):
            for path in sorted(base.glob(f'*/*/{mode}/*/run.json')):
                run=json.loads(path.read_text())
                if run.get('status')!='done':continue
                provider_dir=path.parents[2]
                records={'doc_authorized':load_record(provider_dir/'normalized-result.json','doc_authorized')}
                if mode=='paired-mapping':records['doc_supplement']=load_record(base/'474b22ee4e95075c'/provider_dir.name/'normalized-result.json','doc_supplement')
                intervals=defaultdict(list)
                trajectory=json.loads((path.parent/'messages.json').read_text())
                for message in trajectory:
                    for part in message.get('parts',[]):
                        if part.get('part_kind')!='tool-return' or part.get('tool_name')!='read_document':continue
                        value=part['content']
                        if not isinstance(value,dict):continue
                        document=value['document']['document_id'] if isinstance(value['document'],dict) else value['document']
                        record=records[document]
                        for index,page in enumerate(value['pages']):
                            position=value['offset']-1+index;start=page['char_offset'];end=start+len(page['content'])
                            assert page['content']==record.parsed.pages[position].content[start:end]
                            intervals[(record.document_id,position)].append((start,end))
                complete=True;characters=0
                for doc,record in records.items():
                    for index,page in enumerate(record.parsed.pages):
                        boundary=0
                        for start,end in sorted(intervals[(doc,index)]):
                            if start>boundary:complete=False
                            boundary=max(boundary,end)
                        if boundary!=len(page.content):complete=False
                        characters+=len(page.content)
                if not complete:print('Incomplete model read:',trial,provider_dir.name,provider_dir.parent.name,mode,path.parent.name)
                summaries.append({'trial':trial,'provider':provider_dir.name,'case_hash':provider_dir.parent.name,'mode':mode,'stage':path.parent.name,'full_characters_read':characters,'tool_calls':len(run['calls']),'complete':complete})
    save(ROOT/'local-private/model-full-read-audit.json',summaries)
    print('Actual model full native-content reads:',sum(x['complete']for x in summaries),'/',len(summaries),'passed')


if __name__=='__main__':main()
