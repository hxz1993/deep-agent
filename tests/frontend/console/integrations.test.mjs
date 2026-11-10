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
function harness(fetcher=async path=>path.endsWith('/api')?spec:path.endsWith('/agents')?agents:path.endsWith('/app-configs')?[]:{groups:[group()]}, timers={setTimeout,clearTimeout}) {
 const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',hidden:false,open:false,disabled:false,inert:true,classList:{add(){},remove(){},toggle(){},contains(){return true}},focus(){},setAttribute(){},replaceChildren(){this.innerHTML=''},querySelector(selector){return selector.includes('integrationAgentId') && node('integrationAgentId').value ? node('integrationAgentId') : null},querySelectorAll(){return[]},addEventListener(){}});return nodes.get(id)};
 const errors=[],requests=[],toasts=[];
 const ctx=vm.createContext({window:{},document:{getElementById:node,addEventListener(){}},AbortController,Option:class {constructor(text,value){Object.assign(this,{text,value})}},...timers,
 escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
 apiFetch(path,options={}){requests.push({path,options});return fetcher(path,options)},
 AdminFormFeedback:{clear(){errors.length=0},show(form,error){errors.push(error.message)},setSubmitting(){}},showToast(message){toasts.push(message)}});
 vm.runInContext(source,ctx);return {admin:ctx.window.integrationsAdmin,editors:ctx.window.DeepAgentIntegrationEditors,node,errors,requests,toasts};
}
const normalize=v=>JSON.parse(JSON.stringify(v));
test('关联 Agent 直接展开选项，底部提供清空与保存',()=>{
 const panel=template.split('id="panel-integrations"')[1].split('</section>')[0];
 assert.match(panel,/id="integrationDirectory"/);
 assert.match(panel,/class="console-select-menu integration-agent-menu hidden"/);
 assert.doesNotMatch(panel.split('id="integrationAgentPopover"')[1].split('</aside>')[0], /<header|<select/);
 assert.match(panel,/<footer>.*清空.*保存<\/button><\/footer>/);
 assert.doesNotMatch(template,/id="integrationConfigModal"|id="addIntegrationEntryBtn"/);
});
test('集成触发器显示真实 Agent 名称或未配置，打开后回填当前值',async()=>{
 const h=harness();await h.admin.refresh();
 assert.deepEqual(Object.keys(h.editors),['deeptable-assistant','deepcube-assistant']);
 assert.equal(h.node('integrationAgentName').textContent,'未配置');
 h.admin.find('deeptable-assistant').settings={agent_id:'agent-a'};h.admin.render();
 assert.equal(h.node('integrationAgentName').textContent,'电子表格分析');
 assert.equal(h.editors['deepcube-assistant'].summary({agent_id:'deleted-id'},agents),'Agent 已不可用（deleted-）');
 h.admin.openConfig('deeptable-assistant');assert.match(h.node('integrationEditor').innerHTML,/value="agent-a" checked/);
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
 assert.equal(h.admin.find('deeptable-assistant').settings.agent_id,'agent-b');assert.equal(h.node('integrationAgentPopover').inert,true);
 h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='';await h.admin.save({preventDefault(){}});
 assert.deepEqual(normalize(h.admin.find('deeptable-assistant').settings),{});
});
test('未知或已删除Agent不能误保存，也不会自动选第一个Agent',async()=>{
 const h=harness();h.admin.items=group({agent_id:'deleted'}).integrations;h.admin.agents=agents;h.admin.openConfig('deeptable-assistant');
 assert.doesNotMatch(h.node('integrationEditor').innerHTML,/checked|value="deleted"/);
 await h.admin.save({preventDefault(){}});
 assert.match(h.errors[0],/请选择可用/);
 h.node('integrationAgentId').value='deleted';await h.admin.save({preventDefault(){}});
 assert.match(h.errors[0],/请选择可用/);assert.equal(h.requests.filter(r=>r.options.method==='PUT').length,0);
});
test('保存期间阻止重复提交和离开，失败保留选择并解锁',async()=>{
 const d=deferred();const h=harness((p,o)=>o.method==='PUT'?d.promise:Promise.resolve(spec));h.admin.items=group().integrations;h.admin.agents=agents;h.admin.openConfig('deeptable-assistant');h.node('integrationAgentId').value='agent-a';
 const pending=h.admin.save({preventDefault(){}});assert.equal(h.admin.deactivate(),false);assert.equal(h.admin.closeConfig(),false);
 await h.admin.save({preventDefault(){}});assert.equal(h.requests.filter(r=>r.options.method==='PUT').length,1);
 d.reject(new Error('保存失败'));await pending;assert.equal(h.admin.busy,false);assert.equal(h.node('integrationAgentPopover').inert,false);assert.equal(h.node('integrationAgentId').value,'agent-a');
});
test('切页中止目录和示例读取，迟到内容不会恢复',async()=>{
 const d=deferred();const h=harness(()=>d.promise);const pending=h.admin.refresh();h.admin.deactivate();d.resolve({groups:[group()]});await pending;assert.equal(h.admin.items.length,0);
 const a=deferred();const k=harness(()=>a.promise);k.admin.items=group().integrations;k.admin.select('deeptable-assistant');k.admin.openTab('api');k.admin.deactivate();a.resolve(spec);await new Promise(r=>setImmediate(r));assert.doesNotMatch(k.node('integrationApiContent').innerHTML,/返回/);
});
test('示例与配置同屏，只有请求/返回，失败可重试且内容转义',async()=>{
 let count=0;const h=harness(async()=>{if(!count++)throw new Error('临时失败');return spec});
 await h.admin.loadApi('deeptable-assistant');assert.equal(h.node('retryIntegrationApiBtn').hidden,false);
 await h.admin.loadApi('deeptable-assistant');assert.equal(h.node('retryIntegrationApiBtn').hidden,true);
 const html=h.node('integrationApiContent').innerHTML;assert.match(html,/请求/);assert.match(html,/返回/);assert.match(html,/&lt;example&gt;/);assert.doesNotMatch(html,/401|不应出现在弹框|错误响应/);
 assert.equal(h.requests[0].options.reportStatus,false);
});
test('未知集成不会使用Agent表单作为默认编辑器',()=>{
 const h=harness();h.admin.items=[{code:'future',name:'其他',settings:{}}];h.admin.select('future');h.admin.openConfig('future');assert.equal(h.node('integrationAgentHost').hidden,true);assert.equal(h.admin.editingCode,null);
});
test('集成接口503保留实际错误，不误报数据库未配置',async()=>{
 const core=await readFile(new URL('../../../console/configuration/core.js',import.meta.url),'utf8');const banners=[];
 const ctx=vm.createContext({window:{AdminFormFeedback:{formatDetail:d=>d}},document:{addEventListener(){},getElementById(){return {classList:{toggle(...a){banners.push(a)}}}}},daFetch:async()=>({status:503,ok:false,json:async()=>({detail:'接口暂不可用'})})});vm.runInContext(core,ctx);
 await assert.rejects(vm.runInContext('apiFetch("/api/v1/integrations/deeptable-assistant/api")',ctx),/接口暂不可用/);assert.deepEqual(banners,[]);
});

test('前端目录切换后回显每个集成独立的关联 Agent',async()=>{
 const second={code:'python-server',name:'Python Server',integrations:[{code:'deepcube-assistant',name:'DeepCube 助手',settings:{agent_id:'agent-b'}}]};
 const h=harness(async path=>path.endsWith('/agents')?agents:path.endsWith('/app-configs')?[]:{groups:[group({agent_id:'agent-a'}),second]});
 await h.admin.refresh();assert.equal(h.admin.items.length,2);assert.match(h.node('integrationDirectory').innerHTML,/deeptable-assistant.*deepcube-assistant/);
 assert.equal(h.node('integrationAgentName').textContent,'电子表格分析');
 h.admin.select('deepcube-assistant');assert.equal(h.node('integrationAgentName').textContent,'计算助手');
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
 assert.equal(h.admin.busy,false);assert.equal(cleared,true);assert.equal(h.node('integrationAgentPopover').inert,false);
 assert.match(h.errors[0],/超时，结果待确认/);assert.equal(h.requests.find(r=>r.options.method==='PUT').options.signal.aborted,true);
 assert.equal(h.admin.closeConfig(),true);h.admin.openConfig('deeptable-assistant');
 d.resolve({...group().integrations[0],settings:{agent_id:'agent-b'}});await new Promise(r=>setImmediate(r));
 assert.equal(h.node('integrationAgentPopover').inert,false);assert.equal(h.admin.find('deeptable-assistant').settings.agent_id,'agent-a');assert.deepEqual(h.toasts,[]);
});


test('应用列表只显示专用 Skill 数量，保留整行进入详情', async () => {
 const row = {app_id:'budget001',app_name:'年度预算',space_id:'budget',space_name:'预算空间',tenant_id:'tenant',tenant_name:'企业',skill_count:2};
 const h=harness(async path=>path.endsWith('/app-configs')?[row]:spec);
 h.admin.items=group({agent_id:'agent-a'}).integrations;
 await h.admin.loadApps();
 const html=h.node('integrationAppsBody').innerHTML;
 assert.match(html,/data-integration-app="budget001"/);
 assert.match(html,/data-lucide="book-open-check"><\/i>2/);
 assert.doesNotMatch(html,/追加提示词|详情|测试|<button/);
 h.node('integrationAppSearch').value='不存在';
 h.admin.renderApps();
 assert.equal(h.node('integrationAppsTable').hidden,true);
});

test('保存应用配置后，缓存列表保留详情中已解析的空间名称', async () => {
 const app = { app_id:'budget001', app_name:'年度预算', space_id:'budget',
  space_name:'预算空间', tenant_id:'tenant', tenant_name:'企业', skill_count:2, config_id:'config-1' };
 const h = harness(async () => ({ ...app, space_name:'', prompt_append:'预算口径' }));
 h.admin.setApps([app]);
 h.admin.app = { ...app };
 h.node('integrationAppPrompt').value = '预算口径';
 await h.admin.saveApp();
 assert.equal(h.admin.app.space_name, '预算空间');
 assert.match(h.node('integrationAppsBody').innerHTML, /<td>预算空间<\/td>/);
});

test('离开应用列表中止读取，丢弃迟到配置', async () => {
 const result=deferred();
 const h=harness(()=>result.promise);
 const pending=h.admin.loadApps();
 assert.equal(h.admin.deactivate(),true);
 result.resolve([{app_id:'budget001',app_name:'预算'}]);
 await pending;
 assert.equal(h.admin.apps,null);
});

test('追加提示词计数在输入框外，应用 Skill 预览使用正式容器', () => {
 const detail=template.split('id="integrationAppDetail"')[1];
 assert.match(detail,/integrationAppPromptCount[^]*?<textarea id="integrationAppPrompt"/);
 assert.match(detail,/integration-skill-dialog/);
 assert.match(detail,/移除应用配置/);
});

test('添加应用的搜索能直接找到尚未显示的应用', async () => {
 const apps = Array.from({length: 101}, (_, index) => ({
  app_id: `space${index}`, app_name: `应用 ${index}`, space_id: 'space',
  space_name: '测试空间', tenant_id: 'tenant', tenant_name: '测试企业',
 }));
 const h = harness(async () => apps);
 const picker = h.admin.appPicker = {apps: [], tenant: '', selection: null, limit: 100};
 const options = h.node('integrationAppPickerOptions');
 options.parentElement = {scrollTop: 0};
 await h.admin.loadAppChoices(picker);
 assert.equal(h.node('moreIntegrationAppPickerBtn').hidden, false);
 assert.doesNotMatch(options.innerHTML, /value="space100"/);

 h.node('integrationAppPickerInput').value = '测试空间 space100';
 h.admin.renderAppChoices(picker);
 assert.match(options.innerHTML, /value="space100"/);
 assert.equal(h.node('moreIntegrationAppPickerBtn').hidden, true);
 assert.equal(h.requests.length, 1);
});

test('加载或重试失败时输入筛选，不会提前显示空状态', async () => {
 const pending=deferred();
 const h=harness(()=>pending.promise);
 const loading=h.admin.loadApps();
 h.node('integrationAppSearch').value='预算';
 h.admin.renderApps();
 assert.equal(h.node('integrationAppsStatus').textContent,'正在加载应用配置…');
 pending.reject(new Error('连接失败'));
 await loading;
 h.admin.renderApps();
 assert.equal(h.node('integrationAppsStatus').textContent,'连接失败');
 assert.equal(h.node('retryIntegrationAppsBtn').hidden,false);
});
