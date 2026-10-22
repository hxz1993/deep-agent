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
  assert.equal(requests[0].allow_text_output,false);
});

test('复制 Agent 保留独立的允许文本输出开关以及关闭状态下的定义草稿', async () => {
  for (const enabled of [false, true]) {
    const requests=[];
    const output={structured_output_enabled:enabled,allow_text_output:true,output_config:{name:'result',description:null,json_schema:{type:'object',properties:{},required:[]}}};
    const agent={name:'Agent',model_id:null,prompt:'P',capabilities:[],skills:[]};
    const ctx=vm.createContext({window:{},document:{addEventListener(){}},apiFetch:async(path,opts)=>{
      if(opts){requests.push(JSON.parse(opts.body));return {};}
      if(path.endsWith('/output-config'))return output;
      return path.endsWith('/agents') ? [] : agent;
    },requestActionFields:async()=>({name:'副本'}),showToast(){}});
    vm.runInContext(source,ctx);
    ctx.window.agentsAdmin.refresh=async()=>{};
    await ctx.window.agentsAdmin.copy('source');
    assert.equal(requests.length,1);
    assert.equal(requests[0].structured_output_enabled,enabled);
    assert.equal(requests[0].allow_text_output,true);
    assert.deepEqual(requests[0].output_config,output.output_config);
  }
});


test('分类总控跟随全选、部分选中和空列表', () => {
  const inputs = [{checked:false,value:'a'},{checked:false,value:'b'}];
  const status = {};
  const panel = {querySelectorAll:()=>inputs, querySelector:()=>status};
  const toggle = {closest:()=>panel};
  const document = {addEventListener(){}, querySelector:()=>panel, querySelectorAll:(selector)=>
    selector === '[data-cap-group-toggle]' ? [toggle] : selector.includes(':checked') ? inputs.filter(x=>x.checked) : []};
  const ctx = vm.createContext({window:{},document,state:{capabilities:[]}});
  vm.runInContext(source,ctx);
  const admin=ctx.window.agentsAdmin;
  admin.updateCapCounter();
  assert.equal(toggle.checked,false);
  assert.equal(toggle.indeterminate,false);
  inputs[0].checked=true;
  admin.updateCapCounter();
  assert.equal(toggle.indeterminate,true);
  assert.equal(status.textContent,'已启用 1 / 2');
  admin.setCapsInKind('builtin',true);
  assert.equal(toggle.checked,true);
  assert.equal(toggle.indeterminate,false);
  assert.equal(status.textContent,'已全部启用');
  admin.setCapsInKind('builtin',false);
  assert.equal(status.textContent,'未启用');
  inputs.length=0;
  admin.updateCapCounter();
  assert.equal(toggle.disabled,true);
  assert.equal(status.textContent,'暂无可选项');
});
