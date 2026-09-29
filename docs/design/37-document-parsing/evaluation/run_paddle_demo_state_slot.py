import json,time,subprocess,re
from pathlib import Path
p=Path(__file__).parent
host='https://paddlepaddle-paddleocr-vl-1-6-online-demo.hf.space'
t=time.monotonic()
def curl(args,timeout=30):return subprocess.check_output(['curl','-sS','--max-time',str(timeout),*args])
u=json.loads(curl([host+'/gradio_api/upload','-F','files=@'+str(p/'fixture-preview.png')]))
f={'path':u[0],'orig_name':'fixture-page.png','meta':{'_type':'gradio.FileData'}}
r=json.loads(curl([host+'/gradio_api/call/parse_doc','-H','Content-Type: application/json','--data-binary',json.dumps({'data':[f,None,True,False,False]})])); print('submitted',r,round(time.monotonic()-t,2),flush=True)
try:
 out=curl([host+'/gradio_api/call/parse_doc/'+r['event_id']],120).decode()
except subprocess.CalledProcessError as e:out=e.output.decode();out+='\nTIMEOUT_OR_CURL_ERROR '+str(e.returncode)
out=re.sub(r'https://[^\"\s\\]+authorization=[^\"\s\\]+', '[SIGNED_VISUALIZATION_URL_REDACTED]', out)
(p/'paddle16-demo-sse-state-slot.txt').write_text(out)
(p/'paddle16-demo-run-state-slot.json').write_text(json.dumps({'host':host,'space':'PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo','fixture':'fixture-page.png','chart':True,'unwarp':False,'orientation':False,'elapsed_s':round(time.monotonic()-t,3),'response':r},indent=2))
print(out[:12000]);print('elapsed',round(time.monotonic()-t,2))
for block in out.split('\n\n'):
 if 'event: complete' in block:
  data=json.loads(block.split('data: ',1)[1]);(p/'paddle16-fixture.md').write_text(data[2]);break
