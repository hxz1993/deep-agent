import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
const source = await readFile(new URL('../../../console/configuration/integrations.js', import.meta.url), 'utf8');
const template = await readFile(new URL('../../../console/configuration-template.html', import.meta.url), 'utf8');
function deferred() { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; }
const agents=[{id:'agent-a',name:'电子表格分析'},{id:'agent-b',name:'计算助手'}];
const group=(settings={})=>({code:'deeptable',name:'电子表格',icon:'table-2',integrations:[{code:'deeptable-assistant',name:'电子表格助手',settings}]});
const spec={description:'读取此集成使用的 Agent',method:'GET',path:'/api/v1/integrations/deeptable-assistant',response_example:{code:'deeptable-assistant',settings:{agent_id:'<example>'}},errors:[{status:401,message:'不应出现在弹框'}]};
function harness(fetcher=async path=>path.endsWith('/api')?spec:path.endsWith('/agents')?agents:{groups:[group()]}, timers={setTimeout,clearTimeout}) {
 const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',hidden:false,open:false,disabled:false,showModal(){this.open=true},close(){this.open=false},replaceChildren(){this.innerHTML=''},querySelectorAll(){return[]},addEventListener(){}});return nodes.get(id)};
 const errors=[],requests=[],toasts=[];
 const ctx=vm.createContext({window:{},document:{getElementById:node,addEventListener(){}},AbortController,...timers,
 escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
 apiFetch(path,options={}){requests.push({path,options});return fetcher(path,options)},
 AdminFormFeedback:{clear(){errors.length=0},show(form,error){errors.push(error.message)},setSubmitting(){}},showToast(message){toasts.push(message)}});
 vm.runInContext(source,ctx);return {admin:ctx.window.integrationsAdmin,editors:ctx.window.DeepAgentIntegrationEditors,node,errors,requests,toasts};
}
const normalize=v=>JSON.parse(JSON.stringify(v));
test('一个弹框合并接口和配置，表格展示配置，无通用KV编辑器',()=>{
 const panel=template.split('id="panel-integrations"')[1].split('</section>')[0];
 assert.match(panel,/<th>配置<\/th>/);
 assert.equal((template.match(/id="integrationConfigModal"/g)||[]).length,1);
 assert.doesNotMatch(template,/id="integrationApiModal"|id="addIntegrationEntryBtn"/);
 assert.match(template,/integration-example-pane/);assert.match(template,/integration-editor-pane/);
});
test('两种集成各自选择渲染器，表格展示真实Agent名称及未配置/已删除状态',async()=>{
 const h=harness();await h.admin.refresh();
 assert.deepEqual(Object.keys(h.editors),['deeptable-assistant','deepcube-assistant']);
 assert.match(h.node('integrationTableBody').innerHTML,/未配置/);
 h.admin.find('deeptable-assistant').settings={agent_id:'agent-a'};h.admin.render();
 assert.match(h.node('integrationTableBody').innerHTML,/电子表格分析/);
 assert.equal(h.editors['deepcube-assistant'].summary({agent_id:'deleted-id'},agents),'Agent 已不可用（deleted-）');
 h.admin.openConfig('deeptable-assistant');assert.match(h.node('integrationEditor').innerHTML,/value="agent-a" selected/);
 assert.doesNotMatch(h.node('integrationEditor').innerHTML,/textarea|Key|Value/);
});
test('保存所选Agent并保留其他JSON字段，取消不写入，选未配置清除',async()=>{
 const h=harness(async(path,options)=>options.method==='PUT'?{...group().integrations[0],settings:JSON.parse(options.body).settings}:spec);
 h.admin.items=group({agent_id:'agent-a',other:{flag:true}}).integrations;h.admin.agents=agents;
 h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='agent-b';h.admin.closeConfig();
 assert.equal(h.requests.filter(r=>r.options.method==='PUT').length,0);
 h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='agent-b';await h.admin.save({preventDefault(){}});
 assert.equal(h.requests.find(r=>r.options.method==='PUT').path,'/api/v1/integrations/deeptable-assistant');
 assert.deepEqual(normalize(h.admin.find('deeptable-assistant').settings),{agent_id:'agent-b',other:{flag:true}});
 assert.match(h.node('integrationTableBody').innerHTML,/计算助手/);assert.equal(h.node('integrationConfigModal').open,false);
 h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='';await h.admin.save({preventDefault(){}});
 assert.deepEqual(normalize(h.admin.find('deeptable-assistant').settings),{});
});
test('未知或已删除Agent不能误保存，也不会自动选第一个Agent',async()=>{
 const h=harness();h.admin.items=group({agent_id:'deleted'}).integrations;h.admin.agents=agents;h.admin.openConfig('deeptable-assistant');
 assert.match(h.node('integrationEditor').innerHTML,/selected disabled/);
 h.node('integrationAgentId').value='deleted';await h.admin.save({preventDefault(){}});
 assert.match(h.errors[0],/请选择可用/);assert.equal(h.requests.filter(r=>r.options.method==='PUT').length,0);
});
test('保存期间阻止重复提交和离开，失败保留选择并解锁',async()=>{
 const d=deferred();const h=harness((p,o)=>o.method==='PUT'?d.promise:Promise.resolve(spec));h.admin.items=group().integrations;h.admin.agents=agents;h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='agent-a';
 const pending=h.admin.save({preventDefault(){}});assert.equal(h.admin.deactivate(),false);assert.equal(h.admin.closeConfig(),false);
 await h.admin.save({preventDefault(){}});assert.equal(h.requests.filter(r=>r.options.method==='PUT').length,1);
 d.reject(new Error('保存失败'));await pending;assert.equal(h.admin.busy,false);assert.equal(h.node('integrationConfigModal').open,true);assert.equal(h.node('integrationAgentId').value,'agent-a');
});
test('切页中止目录和示例读取，迟到内容不会恢复',async()=>{
 const d=deferred();const h=harness(()=>d.promise);const pending=h.admin.refresh();h.admin.deactivate();d.resolve({groups:[group()]});await pending;assert.equal(h.admin.items.length,0);
 const a=deferred();const k=harness(()=>a.promise);k.admin.items=group().integrations;k.admin.openConfig('deeptable-assistant');k.admin.closeConfig();a.resolve(spec);await new Promise(r=>setImmediate(r));assert.doesNotMatch(k.node('integrationApiContent').innerHTML,/返回/);
});
test('示例与配置同屏，只有请求/返回，失败可重试且内容转义',async()=>{
 let count=0;const h=harness(async()=>{if(!count++)throw new Error('临时失败');return spec});
 await h.admin.loadApi('deeptable-assistant');assert.equal(h.node('retryIntegrationApiBtn').hidden,false);
 await h.admin.loadApi('deeptable-assistant');assert.equal(h.node('retryIntegrationApiBtn').hidden,true);
 const html=h.node('integrationApiContent').innerHTML;assert.match(html,/请求/);assert.match(html,/返回/);assert.match(html,/&lt;example&gt;/);assert.doesNotMatch(html,/401|不应出现在弹框|错误响应/);
 assert.equal(h.requests[0].options.reportStatus,false);
});
test('未知集成不会使用Agent表单作为默认编辑器',()=>{
 const h=harness();h.admin.items=[{code:'future',name:'其他',settings:{}}];h.admin.openConfig('future');assert.match(h.node('integrationEditor').innerHTML,/暂不支持配置/);assert.equal(h.node('saveIntegrationConfigBtn').disabled,true);
});
test('集成接口503保留实际错误，不误报数据库未配置',async()=>{
 const core=await readFile(new URL('../../../console/configuration/core.js',import.meta.url),'utf8');const banners=[];
 const ctx=vm.createContext({window:{AdminFormFeedback:{formatDetail:d=>d}},document:{addEventListener(){},getElementById(){return {classList:{toggle(...a){banners.push(a)}}}}},daFetch:async()=>({status:503,ok:false,json:async()=>({detail:'接口暂不可用'})})});vm.runInContext(core,ctx);
 await assert.rejects(vm.runInContext('apiFetch("/api/v1/integrations/deeptable-assistant/api")',ctx),/接口暂不可用/);assert.deepEqual(banners,[]);
});

test('前端将目录条目平铺为普通表格，保留不同编码的配置和编辑入口',async()=>{
 const second={code:'python-server',name:'Python Server',integrations:[{code:'deepcube-assistant',name:'DeepCube 助手',settings:{agent_id:'agent-b'}}]};
 const h=harness(async path=>path.endsWith('/agents')?agents:{groups:[group({agent_id:'agent-a'}),second]});
 await h.admin.refresh();assert.equal(h.admin.items.length,2);assert.doesNotMatch(template,/id="integrationGroups"|id="integrationGroupName"|integration-workspace/);
 assert.match(h.node('integrationTableBody').innerHTML,/电子表格分析/);assert.match(h.node('integrationTableBody').innerHTML,/计算助手/);
 assert.deepEqual(normalize(h.admin.find('deepcube-assistant').settings),{agent_id:'agent-b'});
});


test('保存超时解锁并提示结果待确认，迟到成功不覆盖后续编辑',async()=>{
 const d=deferred();let expire,cleared=false;
 const h=harness((path,options)=>options.method==='PUT'?d.promise:Promise.resolve(spec),{
  setTimeout(fn,ms){assert.equal(ms,30000);expire=fn;return 1},clearTimeout(id){assert.equal(id,1);cleared=true},
 });
 h.admin.items=group({agent_id:'agent-a'}).integrations;h.admin.agents=agents;
 h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='agent-b';
 const pending=h.admin.save({preventDefault(){}});assert.equal(h.admin.busy,true);
 expire();await pending;
 assert.equal(h.admin.busy,false);assert.equal(cleared,true);assert.equal(h.node('integrationConfigModal').open,true);
 assert.match(h.errors[0],/超时，结果待确认/);assert.equal(h.requests.find(r=>r.options.method==='PUT').options.signal.aborted,true);
 assert.equal(h.admin.closeConfig(),true);h.admin.openConfig('deeptable-assistant');
 d.resolve({...group().integrations[0],settings:{agent_id:'agent-b'}});await new Promise(r=>setImmediate(r));
 assert.equal(h.node('integrationConfigModal').open,true);assert.equal(h.admin.find('deeptable-assistant').settings.agent_id,'agent-a');assert.deepEqual(h.toasts,[]);
});
