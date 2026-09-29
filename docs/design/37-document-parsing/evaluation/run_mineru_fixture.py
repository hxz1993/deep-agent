import json,time,subprocess
from pathlib import Path
base=Path(__file__).parent
fixture=base/'complex-numbers-two-pages.pdf'
t0=time.monotonic()
def call(url,payload=None,upload=None):
 cmd=['curl','-sS','--max-time','45','--retry','1',url]
 if payload is not None:cmd+=['-H','Content-Type: application/json','--data-binary',json.dumps(payload)]
 if upload:cmd+=['-X','PUT','--upload-file',str(upload)]
 return subprocess.check_output(cmd)
payload={'file_name':fixture.name,'language':'ch','enable_table':True,'enable_formula':True,'is_ocr':False}
r=json.loads(call('https://mineru.net/api/v1/agent/parse/file',payload));d=r.get('data',{})
record={'request':payload,'submitted_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'submit_elapsed_s':round(time.monotonic()-t0,3),'submit':{k:v for k,v in r.items() if k!='data'},'task_id':d.get('task_id'),'polls':[]}
if not d.get('file_url'):raise RuntimeError(r)
call(d['file_url'],upload=fixture)
record['upload_finished_elapsed_s']=round(time.monotonic()-t0,3)
for i in range(40):
 r=json.loads(call('https://mineru.net/api/v1/agent/parse/'+d['task_id']))
 state=r.get('data',{}).get('state');record['polls'].append({'elapsed_s':round(time.monotonic()-t0,3),'state':state})
 print(record['polls'][-1],flush=True)
 if state in ('done','failed'):
  record['result']={k:v for k,v in r.get('data',{}).items() if not k.endswith('url')}
  if state=='done':
   md=call(r['data']['markdown_url']);(base/'mineru-complex-numbers.md').write_bytes(md);record['output_bytes']=len(md)
  break
 time.sleep(3)
record['total_elapsed_s']=round(time.monotonic()-t0,3)
(base/'mineru-complex-numbers-run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
print(json.dumps(record,ensure_ascii=False,indent=2))
