import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../../console/configuration/agent-package-export.js', import.meta.url), 'utf8');
const agent = {id:'agent',name:'验收<Agent>',prompt:'<prompt>',skills:['uploaded','builtin'],capabilities:['ui:custom','mcp:optional','builtin:sandbox'],model:null};
const skills = [{code:'uploaded',label:'完整 Skill',source:'upload'},{code:'builtin',label:'内置 Skill',source:'builtin'}];
const packages = {items:[{code:'ui:custom',label:'自定义 UI',source:'manual',tool_count:2}]};
const tools = {items:[{name:'form_fill',package_code:'ui:custom',metadata:{display_name:'配置填充'}},{name:'clarify',package_code:'ui:custom'}]};
const response = value => ({ok:true,json:async()=>value,blob:async()=>value});
const dataFor = path => path.endsWith('/export') ? new Blob(['actual ZIP']) : path.endsWith('/skills') ? skills : path.endsWith('/ui-tool-packages') ? packages : path.endsWith('/ui-tools') ? tools : agent;
function harness(fetcher = async path => response(dataFor(path))) {
 const nodes=new Map(),calls=[],downloads=[],toasts=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{id,innerHTML:'',textContent:'',dataset:{},disabled:false,classList:{toggle(){}},showModal(){this.open=true},close(){this.open=false},addEventListener(){},click(){downloads.push(this.download)},remove(){}});return nodes.get(id)};
 const window={addEventListener(){},AdminFormFeedback:{formatDetail:String}};
 const context=vm.createContext({window,document:{getElementById:node,createElement:()=>node('anchor'),body:{appendChild(){}},addEventListener(){}},AbortController,URL:{createObjectURL:()=> 'blob:actual',revokeObjectURL(){}},setTimeout,clearTimeout,serviceUrl:path=>path,daFetch:async(path,options)=>{calls.push({path,...options});return fetcher(path,options)},escapeHtml:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),showToast:m=>toasts.push(m)});
 vm.runInContext(source,context);return{admin:window.agentPackageExportAdmin,node,calls,downloads,toasts};
}
test('导出确认仅加载真实概览，区分完整资源与参考项并转义内容',async()=>{
 const h=harness();await h.admin.open('agent');assert.equal(h.calls.length,4);assert.equal(h.downloads.length,0);
 assert.equal(h.node('agentExportModal').open,true);assert.equal(h.node('confirmAgentExportBtn').disabled,false);
 const html=h.node('agentExportContent').innerHTML;assert.match(html,/验收&lt;Agent&gt;/);assert.match(html,/<b>2<\/b> Skill/);assert.match(html,/<b>2<\/b> UI 工具/);assert.match(html,/配置填充、clarify/);assert.match(html,/仅参考/);assert.match(html,/内置引用/);assert.equal((html.match(/<td>Skill<\/td>/g)||[]).length,1);assert.doesNotMatch(html,/<prompt>|查看提示词|温度|agent-export-prompt|agent-export-notice/);
});
test('确认后才下载服务返回的真实ZIP，关闭弹窗',async()=>{
 const h=harness();await h.admin.open('agent');await h.admin.downloadConfirmed();assert.equal(h.calls.at(-1).path,'/api/v1/agents/agent/export');assert.deepEqual(h.downloads,['agent-agent.zip']);assert.equal(h.node('agentExportModal').open,false);
});
test('取消概览与迟到响应均不会自动导出',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),h=harness(()=>pending);const opening=h.admin.open('agent');h.admin.close();resolve(response(agent));await opening;
 assert.equal(h.admin.data,null);assert.equal(h.downloads.length,0);assert.ok(h.calls.every(c=>c.signal.aborted));
});
test('关闭导出弹窗后忽略迟到的下载结果',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),h=harness(async path=>path.endsWith('/export')?pending:response(dataFor(path)));
 await h.admin.open('agent');const downloading=h.admin.downloadConfirmed();h.admin.close();resolve(response(new Blob(['late ZIP'])));await downloading;
 assert.equal(h.downloads.length,0);assert.equal(h.node('agentExportModal').open,false);
});
test('缺失资源阻止导出，读取失败就近提示',async()=>{
 const h=harness(async path=>response(path.endsWith('/skills')?[]:dataFor(path)));await h.admin.open('agent');await h.admin.downloadConfirmed();assert.equal(h.node('confirmAgentExportBtn').disabled,true);assert.match(h.node('agentExportStatus').textContent,/缺失/);assert.equal(h.calls.length,4);
 const bad=harness(async()=>({ok:false,status:403,json:async()=>({detail:'无权限'})}));await bad.admin.open('agent');assert.match(bad.node('agentExportStatus').textContent,/无权限/);assert.equal(bad.downloads.length,0);
});
test('导出失败保留确认内容供重试；重复确认不会重复下载',async()=>{
 let resolve;const d=new Promise(r=>resolve=r);const h=harness(async path=>path.endsWith('/export')?d:response(dataFor(path)));await h.admin.open('agent');const first=h.admin.downloadConfirmed();await h.admin.downloadConfirmed();assert.equal(h.calls.filter(c=>c.path.endsWith('/export')).length,1);
 resolve({ok:false,status:422,json:async()=>({detail:'原 Skill 文件不可用'})});await first;
 assert.equal(h.downloads.length,0);assert.equal(h.node('agentExportModal').open,true);assert.match(h.node('agentExportStatus').textContent,/原 Skill 文件不可用/);assert.equal(h.node('confirmAgentExportBtn').disabled,false);
});

test('工具范围与整包定义分别展示，空名单不伪造成全部工具', async()=>{
 const scoped={...agent,ui_tool_selection:{'ui:custom':['clarify']}};
 const h=harness(async path=>response(path.endsWith('/agent')?scoped:dataFor(path)));await h.admin.open('agent');
 assert.equal(h.admin.data.tools.length,2);assert.equal(h.admin.data.selectedTools.length,1);assert.match(h.node('agentExportContent').innerHTML,/Agent 工具范围[\s\S]*clarify[\s\S]*1 \/ 2 已选/);
 scoped.ui_tool_selection['ui:custom']=[];await h.admin.open('agent');assert.equal(h.admin.data.selectedTools.length,0);assert.match(h.node('agentExportContent').innerHTML,/未选择 UI 工具/);
});
