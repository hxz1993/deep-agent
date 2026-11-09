import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('../../../console/developer.js', import.meta.url), 'utf8');
const { createResourcesController, codingPrompt, resourceSummary } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const items = ['initialization-guide','development-skill'].map((code,i)=>({code, filename: i ? 'skill.zip':'guide.pdf',media_type:i?'application/zip':'application/pdf',size_bytes:3,sha256:'a'.repeat(64),page_count:10,document_count:12,version:'0.15.11'}));
const gate=()=>{let resolve; const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
function harness(request) {
 const events=[],saved=[]; const ctl=createResourcesController({request,url:p=>`http://local/prefix/${p}`,saveBlob:(b,n)=>saved.push([b.size,n]),view:{busy:(...a)=>events.push(['busy',...a]),error:(...a)=>events.push(['error',...a]),catalog:v=>events.push(['catalog',v])}});
 return {ctl,events,saved};
}
const catalog=()=>Response.json({items});
test('prompt preserves gateway prefix but removes credentials/query and metadata uses real counts',()=>{
 const prompt=codingPrompt('https://user:secret@example.test/service/deep-agent/?token=private#frag');
 assert.ok(prompt.includes('https://example.test/service/deep-agent'));
 assert.match(prompt,/README\.md/);
 assert.doesNotMatch(prompt,/secret|private|download|user:/);
 assert.match(resourceSummary(items[0]),/PDF · 10 页/); assert.match(resourceSummary(items[1]),/ZIP · 12 份资料/);
});
test('metadata is lazy and a deactivated late response cannot replace the next visit',async()=>{
 const first=gate(); let count=0,signal;
 const h=harness((_url,options)=>{signal=options.signal; return ++count===1?first.promise:Promise.resolve(catalog());});
 assert.equal(count,0); h.ctl.setActive(true); h.ctl.setActive(false); assert.equal(signal.aborted,true);
 h.ctl.setActive(true); await tick(); first.resolve(Response.json({items:[]})); await tick();
 assert.equal(h.events.filter(e=>e[0]==='catalog').length,1); assert.equal(h.events.filter(e=>e[0]==='error').length,0);
 h.ctl.setActive(false);
});
test('download is deduplicated, fixed-endpoint, validated and cancelled when leaving',async()=>{
 const d=gate();let calls=0,signal;
 const h=harness((url,opts)=>{ if(url.endsWith('developer-resources'))return Promise.resolve(catalog()); calls++;signal=opts.signal; assert.match(url,/prefix\/api\/v1\/developer-resources\/development-skill\/download$/);return d.promise;});
 h.ctl.setActive(true); await tick(); const one=h.ctl.download('development-skill'); await h.ctl.download('development-skill');assert.equal(calls,1);
 h.ctl.setActive(false);assert.equal(signal.aborted,true); d.resolve(new Response('zip',{headers:{'Content-Type':'application/zip'}}));await one;assert.deepEqual(h.saved,[]);
});
test('valid binary downloads save once while auth/errors/HTML and stale metadata never download',async()=>{
 const variants=[new Response('zip',{headers:{'Content-Type':'application/zip'}}),new Response('',{status:401}),new Response('',{status:403}),new Response('',{status:503}),new Response('<html>',{headers:{'Content-Type':'text/html'}}),new Response('changed',{headers:{'Content-Type':'application/zip'}})];
 for(let i=0;i<variants.length;i++){
  const h=harness(url=>Promise.resolve(url.endsWith('developer-resources')?catalog():variants[i]));h.ctl.setActive(true);await tick();await h.ctl.download('development-skill');
  assert.equal(h.saved.length,i===0?1:0); if(i)assert.equal(h.events.filter(e=>e[0]==='error').length,1);h.ctl.setActive(false);
 }
});
test('transient catalog failures can retry without enabling nonexistent files',async()=>{
 for (const status of [502,503]) {
  let count=0;const h=harness(()=>Promise.resolve(++count===1?new Response('',{status}):catalog()));h.ctl.setActive(true);await tick();
  assert.deepEqual(h.events.find(e=>e[0]==='error'),['error','catalog',`资料请求失败（${status}），请稍后重试。`,true]);
  await h.ctl.download('development-skill');assert.equal(count,1);await h.ctl.reload();assert.equal(h.events.filter(e=>e[0]==='catalog').length,1);h.ctl.setActive(false);
 }
});
test('catalog authentication and release file errors do not offer retry',async()=>{
 const legacy = Response.json({detail:'开发资料暂不可用，请联系管理员检查发行文件'},{status:503});
 const releaseUnavailable = new Response('发行文件校验失败',{status:503,headers:{'X-DeepAgent-Error':'developer_resources_unavailable'}});
 for (const response of [...[401,403,404].map(status=>new Response('',{status})),legacy,releaseUnavailable]) {
  const h=harness(()=>Promise.resolve(response));
  h.ctl.setActive(true);await tick();
  assert.equal(h.events.find(e=>e[0]==='error')[3],false);
  h.ctl.setActive(false);
 }
});
