import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../../console/configuration/agent-packages.js', import.meta.url), 'utf8');
const agentSource = await readFile(new URL('../../../console/configuration/agents.js', import.meta.url), 'utf8');
const template = await readFile(new URL('../../../console/configuration-template.html', import.meta.url), 'utf8');
const fixture = () => ({ mode:'create', target_agent:null, blocked:[], removed_associations:[], digest:'sha', manifest:{exported_from:'0.9.11'}, agent:{name:'安装验收',prompt:'<unsafe>正文'}, resources:[], references:{model:{reference:{name:'模型甲',provider_type:'openai'},matched_id:null},mcps:[{code:'mcp:source',matched_code:null}]},models:[],mcps:[] });
const response = data => ({ok:true,status:200,json:async()=>data,blob:async()=>data});
const normalize = x => JSON.parse(JSON.stringify(x));
function deferred() { let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve}; }
function harness(fetcher=async()=>response(fixture()), timer={setTimeout,clearTimeout}) {
  const nodes=new Map(), requests=[], toasts=[], downloads=[]; let refreshes=0;
  const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,open:false,dataset:{},files:[],classList:{toggle(){}},querySelectorAll(){return[]},setAttribute(){},addEventListener(){},showModal(){this.open=true},close(){this.open=false},click(){downloads.push({href:this.href,download:this.download})},remove(){}});return nodes.get(id)};
  const window={addEventListener(){},agentsAdmin:{async refresh(){refreshes++}},AdminFormFeedback:{formatDetail:d=>typeof d==='string'?d:JSON.stringify(d)}};
  const context=vm.createContext({window,document:{getElementById:node,querySelector:node,querySelectorAll(){return[]},addEventListener(){},createElement:()=>node('anchor'),body:{appendChild(){}}},FormData,Blob,AbortController,...timer,URL:{createObjectURL(){return'blob:test'},revokeObjectURL(){}},serviceUrl:p=>p,daFetch:async(p,o)=>{requests.push({path:p,...o});return fetcher(p,o)},escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),showToast:(m,e)=>toasts.push({message:m,error:e}),loadSkills:async()=>[],loadCapabilities:async()=>[],loadLlmModels:async()=>[]});
  vm.runInContext(source,context); return {admin:window.agentPackagesAdmin,api:window.__AgentPackageUI,node,requests,toasts,downloads,refreshes:()=>refreshes};
}
function upload(h) { h.admin.mode='upload';h.node('agentPackageFile').files=[Object.assign(new Blob(['zip']),{name:'agent.zip'})]; }
function staged(h,data=fixture()) {h.admin.preview=data;h.admin.source={code:'example'};h.admin.choices={name:'安装验收',digest:'sha',mode:'create',target_agent_id:null,model_id:null};h.admin.createName='安装验收';h.admin.step=3;}
test('入口统一安装Agent，卡片列表导出不改变已有操作和配置入口',()=>{
 assert.match(template,/id="installAgentBtn"[^>]*>[\s\S]*?安装 Agent/);
 assert.equal((agentSource.match(/data-export-agent=/g)||[]).length,2);
 assert.match(agentSource,/class="row-action icon-only" data-export-agent=/);
 assert.match(agentSource,/button\.dataset\.exportAgent/);
 assert.match(agentSource,/event\.target\.closest\("a, button"\)/);
 assert.doesNotMatch(template,/<select id="agentModelId" required/);
});
test('预检上传multipart不带JSON Content-Type，模型MCP匹配缺失留空',async()=>{
 const h=harness();upload(h);await h.admin.preflight();assert.equal(h.admin.step,2);
 assert.equal(h.requests[0].path,'/api/v1/agent-packages/preview');
 assert.equal(h.requests[0].body.get('file').size,3);assert.equal(h.requests[0].headers,undefined);
 assert.equal(h.admin.choices.model_id,null);assert.equal(h.requests[0].body.get('mode'),'create');assert.equal(h.admin.choices.mcp_mapping,undefined);
 assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/<details|<pre|data-package-mcp/);assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/<unsafe>/);
});
test('预制目录为空可切上传，选择不存在编码不能预检',async()=>{
 const h=harness(async()=>response({items:[]}));await h.admin.open();
 assert.match(h.node('agentPackageContent').innerHTML,/暂无预制安装包/);
 await h.admin.preflight();assert.match(h.node('agentPackageStatus').textContent,/请选择预制/);
 assert.equal(h.requests.length,2);h.admin.setMode('upload');assert.match(h.node('agentPackageContent').innerHTML,/type="file"/);
});
test('预制包预检和安装传相同来源及摘要，不要求模型MCP',async()=>{
 const h=harness(async p=>response(p.endsWith('/install')?{agent:{id:'new',name:'安装验收',model_id:null},results:[],missing:[],mode:'create'}:fixture()));
 h.admin.items=[{code:'example',name:'演示包'}];h.node('input[name="builtinPackage"]:checked').value='example';
 await h.admin.preflight();assert.equal(h.admin.step,2);h.node('agentPackageName').value='本地安装';h.node('agentPackageModel').value='';await h.admin.next();assert.equal(h.admin.step,3);
 await h.admin.next();
 const req=h.requests.at(-1);assert.equal(req.body.get('builtin_code'),'example');assert.equal(req.body.has('file'),false);
 assert.deepEqual(JSON.parse(req.body.get('options')),{digest:'sha',mode:'create',target_agent_id:null,name:'本地安装',model_id:null});
 assert.equal(h.admin.installed.id,'new');assert.equal(h.refreshes(),1);assert.match(h.node('agentPackageContent').innerHTML,/已新建/);
});
test('共享覆盖需一次确认，缺失仅提示，blocked阻断下一步',async()=>{
 const h=harness(),data=fixture();let installed=false;h.admin.install=async()=>{installed=true};data.resources=[{key:'skill:a',kind:'skill',code:'a',label:'甲',status:'update',affected_agents:[{id:'shared',name:'共用Agent'}]},{key:'builtin_tool:b',kind:'builtin_tool',code:'b',label:'乙',status:'missing'}];staged(h,data);h.admin.render();
 assert.match(h.node('agentPackageContent').innerHTML,/覆盖/);assert.match(h.node('agentPackageContent').innerHTML,/缺失/);assert.match(h.node('agentPackageContent').innerHTML,/共用Agent/);assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/data-package-resource|<select/);
 await h.admin.next();assert.equal(installed,false);assert.match(h.node('agentPackageStatus').textContent,/确认共享/);h.admin.sharedAcknowledged=true;
 await h.admin.next();assert.equal(installed,true);installed=false;data.blocked=['无法写入'];h.admin.render();assert.equal(h.node('agentPackageNextBtn').disabled,true);await h.admin.next();assert.equal(installed,false);
});
test('关闭和来源切换中止预检并忽略迟到结果',async()=>{
 const d=deferred(),h=harness(()=>d.promise);upload(h);const pending=h.admin.preflight();
 const signal=h.requests[0].signal;h.admin.close();assert.equal(signal.aborted,true);d.resolve(response(fixture()));await pending;
 assert.equal(h.admin.preview,null);assert.equal(h.admin.step,1);
});
test('重复安装不重复提交，成功后保留确认结果',async()=>{
 const d=deferred(),h=harness(()=>d.promise);staged(h);const pending=h.admin.install();await h.admin.install();assert.equal(h.requests.length,1);assert.equal(h.admin.close(),false);
 d.resolve(response({agent:{id:'new',name:'安装',model_id:null},results:[],missing:[],mode:'create'}));await pending;assert.equal(h.admin.installed.id,'new');assert.equal(h.node('agentPackageNextBtn').hidden,false);assert.equal(h.node('agentPackageNextBtn').textContent,'确定');assert.equal(h.node('agentPackageSteps').hidden,true);assert.equal(h.node('agentPackageTitle').textContent,'安装完成');assert.equal(h.admin.step,4);
});
test('安装超时解锁并查询列表，迟到成功不能伪造安装完成',async()=>{
 let timeout;const d=deferred(),h=harness(()=>d.promise,{setTimeout:fn=>(timeout=fn,1),clearTimeout(){}});staged(h);
 const pending=h.admin.install();timeout();await pending;
 assert.equal(h.admin.installing,false);assert.equal(h.admin.preview,null);assert.equal(h.refreshes(),1);assert.match(h.node('agentPackageStatus').textContent,/结果待确认/);
 d.resolve(response({id:'late',name:'迟到'}));await new Promise(r=>setImmediate(r));assert.equal(h.admin.installed,null);
});
test('过期摘要或重复名称409要求重新预检，不继续使用旧快照',async()=>{
 const h=harness(async()=>({ok:false,status:409,json:async()=>({detail:'名称重复'})}));staged(h);await h.admin.install();
 assert.equal(h.admin.step,1);assert.equal(h.admin.preview,null);assert.match(h.node('agentPackageStatus').textContent,/名称重复.*重新预检/);
});
test('导出入口委托确认弹窗，不直接下载',async()=>{
 assert.match(source,/window\.agentPackageExportAdmin\.open\(id\)/);
 assert.match(template,/id="agentExportModal"/);
 assert.match(template,/id="confirmAgentExportBtn"/);
});
test('超限或非ZIP文件不发网络请求',async()=>{
 const h=harness();h.admin.mode='upload';h.node('agentPackageFile').files=[{name:'bad.txt',size:1}];await h.admin.preflight();assert.equal(h.requests.length,0);
 h.node('agentPackageFile').files=[{name:'large.zip',size:51*1024*1024}];await h.admin.preflight();assert.equal(h.requests.length,0);assert.match(h.node('agentPackageStatus').textContent,/50 MiB/);
});
test('离开安装后重新打开，旧请求收尾不解锁新安装',async()=>{
 const first=deferred(),second=deferred();let count=0;
 const h=harness(()=>++count===1?first.promise:second.promise);staged(h);
 const old=h.admin.install();h.admin.close(true);staged(h);const current=h.admin.install();
 first.resolve(response({agent:{id:'old',name:'旧'},results:[]}));await old;assert.equal(h.admin.installing,true);assert.equal(h.admin.installed,null);
 second.resolve(response({agent:{id:'new',name:'新'},results:[]}));await current;assert.equal(h.admin.installed.id,'new');assert.equal(h.admin.installing,false);
});
test('普通Agent配置保存空模型发送显式null，名称Prompt仍必填',async()=>{
 const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',open:false,classList:{remove(){},add(){}}});return nodes.get(id)};
 node('agentName').value='无模型';node('agentPrompt').value='正文';const requests=[],errors=[];
 const context=vm.createContext({window:{},document:{getElementById:node,querySelectorAll(){return[]},addEventListener(){}},state:{editingAgentId:'existing'},apiFetch:async(path,opts)=>{requests.push(JSON.parse(opts.body))},showToast(){},AdminFormFeedback:{clear(){},show(f,e){errors.push(e)},setSubmitting(){}},refreshAfterSuccessfulSave:async()=>{}});
 vm.runInContext(agentSource,context);const admin=context.window.agentsAdmin;admin.requestCloseEditor=async()=>{};
 await admin.save({preventDefault(){},currentTarget:{}});assert.equal(requests[0].model_id,null);assert.equal(requests[0].name,'无模型');assert.equal(errors.length,0);
 node('agentPrompt').value='';await admin.save({preventDefault(){},currentTarget:{}});assert.equal(requests.length,1);assert.equal(errors.length,1);
});

test('安装包、配置、检查三步分开，反馈独立且弹窗固定尺寸',async()=>{
 assert.match(template, /<b>1<\/b>选择安装包[^]*<b>2<\/b>配置 Agent[^]*<b>3<\/b>检查并安装/);
 const css=await readFile(new URL('../../../console/agent-packages.css',import.meta.url),'utf8');
 assert.match(css,/dialog#agentPackageModal[^]*?width: 860px; height: 650px/);
 const h=harness();staged(h);h.admin.renderSource();
 assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/安装方式|agentPackageName|agentPackageModel/);
 h.admin.renderConfigure();assert.match(h.node('agentPackageContent').innerHTML,/name="packageOperation"/);assert.match(h.node('agentPackageContent').innerHTML,/value="安装验收"/);
 h.admin.renderPreview();assert.match(h.node('agentPackageContent').innerHTML,/<table[^]*<th>类型<[^]*<th>内容<[^]*<th>处理<[^]*<th>说明/);assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/<details|<pre|id="agentPackageName"|id="agentPackageModel"/);
 assert.equal(h.node('agentPackageStatus').textContent,'');
});
test('选择或拖入安装包立即解析，解析后启用下一步',async()=>{
 const h=harness();h.admin.mode='upload';const file=Object.assign(new Blob(['zip']),{name:'acceptance.zip'});
 await h.admin.chooseFile(file);assert.equal(h.admin.source.file,file);assert.equal(h.node('agentPackageNextBtn').disabled,false);
 assert.match(h.node('agentPackageContent').innerHTML,/agent-package-dropzone[\s\S]*acceptance.zip/);
 await h.admin.chooseFile({name:'bad.txt',size:2});assert.equal(h.admin.source.file.name,'bad.txt');assert.match(h.node('agentPackageContent').innerHTML,/解析失败.*ZIP/);assert.equal(h.node('agentPackageStatus').textContent,'');assert.equal(h.node('agentPackageNextBtn').disabled,true);
});
test('配置返回安装包后仍保留名称和清空模型草稿',async()=>{
 const h=harness();staged(h);upload(h);h.admin.step=2;h.node('agentPackageName').value='改过的名字';h.node('agentPackageModel').value='';
 h.admin.back();assert.equal(h.admin.step,1);assert.equal(h.admin.choices.name,'改过的名字');
 await h.admin.next();assert.equal(h.admin.step,2);assert.match(h.node('agentPackageContent').innerHTML,/改过的名字/);assert.equal(h.admin.choices.model_id,null);
});
test('覆盖必须显式选择目标，提交沿用目标名称且不显示名称输入',async()=>{
 const data=fixture();data.mode='overwrite';data.target_agent={id:'target',name:'现有Agent'};
 const h=harness(async p=>response(p==='/api/v1/agents'?[{id:'target',name:'现有Agent'}]:data));staged(h);upload(h);h.admin.step=2;h.admin.operationMode='overwrite';
 await h.admin.next();assert.equal(h.requests.length,0);assert.match(h.node('agentPackageStatus').textContent,/选择要覆盖/);
 h.admin.agents=[{id:'target',name:'现有Agent'}];h.admin.targetAgentId='target';h.admin.renderConfigure();assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/id="agentPackageName"/);
 h.node('agentPackageName').value='不能用于覆盖的名称';await h.admin.next();assert.equal(h.admin.step,3);
 assert.equal(h.requests[0].body.get('mode'),'overwrite');assert.equal(h.requests[0].body.get('target_agent_id'),'target');assert.equal(h.admin.choices.name,'现有Agent');
});
test('长资源列表完整展示，不分页且保留额外工具和取消关联',()=>{
 const h=harness(),data=fixture();data.resources=Array.from({length:14},(_,i)=>({kind:'skill',code:'code'+i,status:'new'}));data.resources[0].retained_tools=['keep_extra'];data.removed_associations=[{kind:'ui',code:'old',label:'旧工具'}];staged(h,data);h.admin.render();
 const html=h.node('agentPackageContent').innerHTML;
 for(let i=0;i<14;i++)assert.match(html,new RegExp('code'+i));
 assert.match(html,/keep_extra/);assert.match(html,/旧工具/);assert.doesNotMatch(html,/pagination|data-package-page|复用/);
});
test('后端默认匹配模型预选，用户显式留空保留null',async()=>{
 const data=fixture();data.references.model.matched_id='fallback';data.models=[{id:'fallback',name:'默认模型'}];const h=harness(async()=>response(data));upload(h);await h.admin.preflight();assert.equal(h.admin.choices.model_id,'fallback');h.admin.renderConfigure();assert.match(h.node('agentPackageContent').innerHTML,/value="fallback" selected/);h.node('agentPackageName').value='Agent';h.node('agentPackageModel').value='';assert.equal(h.admin.readChoices().model_id,null);
});

test('UI 工具逐项显示变更，目标额外工具按整包可用提示',()=>{
 const h=harness();const data=fixture();data.resources=[{kind:'ui',code:'ui:demo',label:'演示工具包',status:'update',tools:[{name:'a',status:'new'},{name:'b',status:'update'},{name:'c',status:'reuse'}],retained_tools:['d']}];staged(h,data);h.admin.render();
 const html=h.node('agentPackageContent').innerHTML;
 assert.match(html,/a<[^]*?新建/);assert.match(html,/b<[^]*?覆盖/);assert.match(html,/c<[^]*?保持不变/);assert.match(html,/d<[^]*?保留[^]*?整包启用后可用/);
});
test('安装结果独立展示，名称模型回显且确定关闭不重新提交',async()=>{
 const h=harness(async()=>response({agent:{id:'new',name:'完成名称',model_id:'m'},results:[{kind:'agent',label:'完成名称',status:'new'},{kind:'skill',label:'技能甲',status:'reuse'}],missing:[],mode:'create'}));
 const data=fixture();data.models=[{id:'m',name:'实际模型'}];staged(h,data);await h.admin.install();
 const html=h.node('agentPackageContent').innerHTML;
 assert.match(html,/agent-package-completed[^]*已新建 完成名称[^]*实际模型/);assert.match(html,/技能甲[^]*保持不变/);assert.doesNotMatch(html,/配置集成|success-link|复用/);
 assert.equal(h.node('agentPackageConfigureBtn').hidden,false);await h.admin.next();assert.equal(h.requests.length,1);assert.equal(h.node('agentPackageModal').open,false);
});
test('目标与模型占位不进入菜单，模型为空仍允许安装',()=>{
 const h=harness();staged(h);h.admin.operationMode='overwrite';h.admin.agents=[{id:'a',name:'目标'}];h.admin.renderConfigure();
 let html=h.node('agentPackageContent').innerHTML;assert.match(html,/name="packageOperation" value="create"/);assert.match(html,/name="packageOperation" value="overwrite"/);assert.doesNotMatch(html,/select id="agentPackageOperation"/);assert.match(html,/<option value="" hidden/);
 staged(h);h.admin.operationMode='create';h.admin.renderConfigure();html=h.node('agentPackageContent').innerHTML;assert.match(html,/data-package-unavailable="true"/);assert.doesNotMatch(html,/data-package-clear-model/);assert.match(html,/<option value="" hidden/);
});

test('新建默认包名，重名在配置下一步拦截并保留在第二步',async()=>{
 const h=harness(async p=>response(p==='/api/v1/agents'?[{id:'old',name:'安装验收'}]:fixture()));upload(h);await h.admin.next();
 assert.equal(h.admin.step,2);assert.equal(h.admin.operationMode,'create');assert.equal(h.admin.createName,'安装验收');
 h.node('agentPackageName').value=' 安装验收 ';const count=h.requests.length;await h.admin.next();
 assert.equal(h.admin.step,2);assert.equal(h.requests.length,count);assert.match(h.node('agentPackageStatus').textContent,/已存在.*修改名称或选择覆盖/);
 h.node('agentPackageName').value='新的名称';await h.admin.next();assert.equal(h.admin.step,3);assert.equal(h.admin.choices.name,'新的名称');
});
test('检查前重新读取已有 Agent，捕获配置期间新增的重名',async()=>{
 const h=harness(async p=>response(p==='/api/v1/agents'?[{id:'new',name:'后来重名'}]:fixture()));staged(h);upload(h);h.admin.step=2;h.node('agentPackageName').value='后来重名';
 await h.admin.next();assert.equal(h.admin.step,2);assert.match(h.node('agentPackageStatus').textContent,/已存在/);
});
test('缺失提示仅在对应表格行，状态有语义类名且无分页',()=>{
 const h=harness();staged(h);h.admin.renderPreview();const html=h.node('agentPackageContent').innerHTML;
 assert.match(html,/package-row-missing[^]*?mcp:source[^]*?action-missing[^]*?缺失[^]*?请在 MCP 中配置服务，再到 Agent 中关联/);
 assert.equal(h.node('agentPackageStatus').textContent,'');assert.doesNotMatch(html,/pagination/);
});

test('上传后立即显示包名、Skill 名称和工具数，下一步复用解析结果',async()=>{
 const data=fixture();data.resources=[{kind:'skill',label:'技能甲',status:'new'},{kind:'builtin_skill',label:'内置技能乙',status:'reuse'},{kind:'ui',label:'工具包',status:'new',tools:[{name:'tool_a',status:'new'}]}];
 const h=harness(async p=>response(p==='/api/v1/agents'?[]:data));h.admin.mode='upload';
 await h.admin.chooseFile(Object.assign(new Blob(['zip']),{name:'summary.zip'}));
 assert.equal(h.admin.step,1);assert.equal(h.admin.uploadState,'ready');const html=h.node('agentPackageContent').innerHTML;
 assert.match(html,/agent-package-upload-summary[^]*安装验收[^]*2 Skill · 1 UI 工具[^]*技能甲、内置技能乙[^]*tool_a/);assert.doesNotMatch(html,/<unsafe>|正文/);assert.equal(h.node('agentPackageStatus').textContent,'');
 const count=h.requests.length;await h.admin.next();assert.equal(h.admin.step,2);assert.equal(h.requests.length,count);
});
test('服务端解析错误显示在上传框下，重选有效包后恢复',async()=>{
 let valid=false;const h=harness(async p=>p==='/api/v1/agents'?response([]):valid?response(fixture()):({ok:false,status:400,json:async()=>({detail:'缺少 manifest.json'})}));h.admin.mode='upload';
 await h.admin.chooseFile(Object.assign(new Blob(['bad']),{name:'wrong.zip'}));assert.equal(h.admin.uploadState,'error');assert.equal(h.admin.step,1);assert.equal(h.node('agentPackageNextBtn').disabled,true);assert.match(h.node('agentPackageContent').innerHTML,/agent-package-upload-feedback is-error[^]*缺少 manifest.json/);assert.equal(h.node('agentPackageStatus').textContent,'');
 valid=true;await h.admin.chooseFile(Object.assign(new Blob(['good']),{name:'correct.zip'}));assert.equal(h.admin.uploadState,'ready');assert.equal(h.node('agentPackageNextBtn').disabled,false);assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/缺少 manifest/);
});
test('换来源或重选文件后，迟到的解析结果不能覆盖当前上传摘要',async()=>{
 const old=deferred();let count=0;const h=harness(p=>p==='/api/v1/agents'?response([]):++count===1?old.promise:response({...fixture(),agent:{name:'新包'}}));h.admin.mode='upload';
 const pending=h.admin.chooseFile(Object.assign(new Blob(['old']),{name:'old.zip'}));
 await h.admin.chooseFile(Object.assign(new Blob(['new']),{name:'new.zip'}));old.resolve(response(fixture()));await pending;
 assert.equal(h.admin.preview.agent.name,'新包');assert.equal(h.admin.source.file.name,'new.zip');assert.equal(h.admin.uploadState,'ready');
 const second=deferred();const h2=harness(()=>second.promise);h2.admin.mode='upload';const reading=h2.admin.chooseFile(Object.assign(new Blob(['old']),{name:'old.zip'}));h2.admin.setMode('upload');second.resolve(response(fixture()));await reading;assert.equal(h2.admin.preview,null);assert.equal(h2.admin.uploadState,'idle');
});
for (const targetModel of [null,'existing-model']) test(`覆盖隐藏默认模型且提交保留目标值 ${targetModel}`,async()=>{
 const target={id:'target',name:'目标',model_id:targetModel,model:targetModel?{name:'原默认模型'}:null};
 const data=fixture();data.models=[{id:'package-model',name:'包推荐模型'},{id:'existing-model',name:'原默认模型'}];data.target_agent={id:target.id,name:target.name};
 const h=harness(async p=>response(p==='/api/v1/agents'?[target]:p.endsWith('/install')?{agent:target,results:[],missing:[],mode:'overwrite'}:data));staged(h,data);upload(h);h.admin.step=2;h.admin.operationMode='overwrite';h.admin.agents=[target];h.admin.targetAgentId=target.id;h.admin.choices.model_id='package-model';h.node('agentPackageModel').value='package-model';
 h.admin.renderConfigure();const html=h.node('agentPackageContent').innerHTML;assert.doesNotMatch(html,/id="agentPackageModel"|清空模型/);assert.doesNotMatch(html,/默认模型|agent-package-readonly/);
 await h.admin.next();assert.equal(h.admin.step,3);assert.equal(h.admin.choices.model_id,targetModel);await h.admin.next();const install=h.requests.find(r=>r.path.endsWith('/install'));assert.equal(JSON.parse(install.body.get('options')).model_id,targetModel);
});
test('覆盖目标模型在预检期间改变，要求重新确认而不直接安装',async()=>{
 const old={id:'target',name:'目标',model_id:null},updated={...old,model_id:'changed',model:{name:'修改后的模型'}};const data=fixture();data.target_agent=old;data.models=[{id:'changed',name:'修改后的模型'}];
 const h=harness(async p=>response(p==='/api/v1/agents'?[updated]:data));staged(h,data);upload(h);h.admin.step=2;h.admin.operationMode='overwrite';h.admin.agents=[old];h.admin.targetAgentId='target';
 await h.admin.next();assert.equal(h.admin.step,2);assert.match(h.node('agentPackageStatus').textContent,/默认模型已变化/);assert.doesNotMatch(h.node('agentPackageContent').innerHTML,/id="agentPackageModel"/);
});

test('预制目录只显示存在的提示词版本',async()=>{
 const h=harness(async()=>response({items:[{code:'versioned',name:'电子表格',prompt_version:'V85'},{code:'plain',name:'无版本'}]}));await h.admin.open();
 const html=h.node('agentPackageContent').innerHTML;
 assert.match(html,/提示词 V85/);
 assert.equal((html.match(/class="agent-package-version"/g)||[]).length,1);
});

test('安装摘要显示输出开关和覆盖旧定义的影响',()=>{
 const {api}=harness();
 assert.equal(api.outputInstallSummary({}), '');
 assert.match(api.outputInstallSummary({structured_output:{enabled:true,name:'lease_result'}}),/lease_result.*随包安装定义/);
 assert.match(api.outputInstallSummary({structured_output:{enabled:false,target_enabled:true}}),/将关闭，并移除目标原有输出定义/);
 assert.match(api.outputInstallSummary({structured_output:{enabled:false,target_enabled:false}}),/关闭/);
});
