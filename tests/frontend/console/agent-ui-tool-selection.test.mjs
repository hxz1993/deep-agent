import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../../../console/configuration/agents.js', import.meta.url), 'utf8');
const cap = {code:'ui:forms',category:'ui',tools:[{name:'one'},{name:'two'}]};

test('Agent 卡片仅提供整包开关及分类计数，不渲染逐工具选择', () => {
  const context = vm.createContext({window:{},state:{capabilities:[cap]},document:{addEventListener(){}},escapeHtml:String});
  vm.runInContext(source, context);
  const admin = context.window.agentsAdmin;
  const ui = admin.renderCapCard(cap, new Set(['ui:forms']));
  const mcp = admin.renderCapCard({code:'mcp:demo',category:'mcp',tool_count:11,resource_count:3,tools:[]}, new Set());
  const builtin = admin.renderCapCard({code:'builtin:general',category:'builtin',tool_count:1,tools:[]}, new Set());
  assert.match(ui, /data-lucide="monitor-check"/);
  assert.doesNotMatch(ui, /data-ui-tool|<details|已选/);
  assert.match(mcp, /data-lucide="wrench"/);
  assert.match(mcp, /data-lucide="file-text"[^]*3<\/span>/);
  assert.doesNotMatch(mcp, />3 资源/);
  assert.match(builtin, /data-lucide="hammer"/);
});

test('复制旧 Agent 仅复制启用的包，不发送旧工具名单', async () => {
  const requests=[];
  const agent={name:'Agent',model_id:null,prompt:'P',capabilities:['ui:forms'],skills:[],ui_tool_selection:{'ui:forms':[]}};
  const ctx=vm.createContext({window:{},document:{addEventListener(){}},apiFetch:async(path,opts)=>{
    if(opts){requests.push(JSON.parse(opts.body));return {};}
    return path.endsWith('/agents') ? [] : agent;
  },requestActionFields:async()=>({name:'副本'}),showToast(){}});
  vm.runInContext(source,ctx);
  ctx.window.agentsAdmin.refresh=async()=>{};
  await ctx.window.agentsAdmin.copy('source');
  assert.equal(requests.length,1);
  assert.deepEqual(requests[0].capabilities,['ui:forms']);
  assert.equal(Object.hasOwn(requests[0],'ui_tool_selection'),false);
});
