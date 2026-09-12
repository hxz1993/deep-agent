import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../../../console/configuration/agents.js',import.meta.url),'utf8');
const cap={code:'ui:forms',category:'ui',tools:[{name:'one'},{name:'two'}]};
function harness(){const context=vm.createContext({window:{},state:{capabilities:[cap]},document:{addEventListener(){},querySelectorAll(){return[]}},escapeHtml:String});vm.runInContext(source,context);return context.window.agentsAdmin;}
const normalize=value=>JSON.parse(JSON.stringify(value));
test('旧Agent无范围key保留整包，单工具取消会转为明确名单',()=>{
 const a=harness();a.uiToolSelection={};assert.deepEqual(normalize(a.getSelectedUiToolSelection(['ui:forms'])),{});
 a.setUiToolEnabled('ui:forms','one',false);assert.deepEqual(normalize(a.getSelectedUiToolSelection(['ui:forms'])),{'ui:forms':['two']});
 a.setUiToolEnabled('ui:forms','two',false);assert.deepEqual(normalize(a.getSelectedUiToolSelection(['ui:forms'])),{'ui:forms':[]});
 a.setUiToolEnabled('ui:forms','one',true);assert.deepEqual(normalize(a.getSelectedUiToolSelection(['ui:forms'])),{'ui:forms':['one']});
});
test('未关联包不发送旧范围，不接受不属于包的工具名',()=>{
 const a=harness();a.uiToolSelection={'ui:forms':[]};a.setUiToolEnabled('ui:forms','foreign',true);assert.deepEqual(normalize(a.getSelectedUiToolSelection(['ui:forms'])),{'ui:forms':[]});assert.deepEqual(normalize(a.getSelectedUiToolSelection([])),{});
});
test('范围编辑按单工具回显，空范围与整包状态不同',()=>{
 const a=harness();a.uiToolSelection={};assert.match(a.renderUiToolChoices(cap),/data-ui-tool-all="ui:forms" checked/);
 a.uiToolSelection={'ui:forms':[]};const empty=a.renderUiToolChoices(cap);assert.match(empty,/已选 0 \/ 2/);assert.doesNotMatch(empty,/ checked/);
});
test('复制请求保留工具名单与显式空名单',async()=>{
 const requests=[];const agent={name:'Agent',model_id:null,prompt:'P',capabilities:['ui:forms'],skills:[],ui_tool_selection:{'ui:forms':[]}};
 const ctx=vm.createContext({window:{},document:{addEventListener(){}},apiFetch:async(path,opts)=>{if(opts){requests.push(JSON.parse(opts.body));return{}}return path.endsWith('/agents')?[]:agent;},requestActionFields:async()=>({name:'副本'}),showToast(){}});vm.runInContext(source,ctx);ctx.window.agentsAdmin.refresh=async()=>{};
 await ctx.window.agentsAdmin.copy('source');assert.equal(requests.length,1);assert.deepEqual(requests[0].ui_tool_selection,{'ui:forms':[]});assert.equal(requests[0].model_id,null);
});
