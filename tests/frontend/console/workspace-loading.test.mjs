import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const source=await readFile(new URL('../../../console/workspace.js',import.meta.url),'utf8');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function harness(){
 const gate=deferred();let fetches=0,installs=0;const node={classList:{add(){},remove(){}}};
 const context=vm.createContext({window:{DeepAgentConsoleChat:{ready:gate.promise},addEventListener(){},lucide:{createIcons(){}}},document:{getElementById:()=>node},importChat:async()=>{}});
 vm.runInContext(source.replace(/^import .*;\n/gm,'').replace(/^export /gm,'').replaceAll('import.meta.url',"'http://console.test/console/workspace.js'").replace(/await import\(`\.\/chat\.js\?v=\$\{WORKSPACE_ASSET_VERSION\}`\)/,'await importChat()'),context);
 context.fetchLegacyPage=async()=>{fetches++;return {};};context.installLegacyDom=()=>{installs++;};
 for(const name of ['bindWorkspaceControls','bindWorkspaceResizers','renderAgentSwitcher','renderInspector','startStateMonitor','refreshChannelAccounts','syncWorkspaceLayout'])context[name]=()=>{};
 return {context,gate,get fetches(){return fetches;},get installs(){return installs;}};
}
test('workspace shares initialization and waits for the existing chat directory startup',async()=>{
 const h=harness();let ready=false;const a=h.context.initWorkspace().then(()=>{ready=true;});const b=h.context.initWorkspace();await new Promise(r=>setImmediate(r));assert.equal(ready,false);assert.equal(h.fetches,1);assert.equal(h.installs,1);h.gate.resolve();await Promise.all([a,b]);assert.equal(ready,true);
});
test('template failure may retry, but a partial initialization never becomes a false success',async()=>{
 const h=harness();h.context.fetchLegacyPage=async()=>{throw Error('template unavailable');};await assert.rejects(h.context.initWorkspace(),/template/);h.context.fetchLegacyPage=async()=>({});h.gate.resolve();await h.context.initWorkspace();assert.equal(h.installs,1);
 const broken=harness();broken.context.importChat=async()=>{throw Error('module unavailable');};await assert.rejects(broken.context.initWorkspace(),/module/);await assert.rejects(broken.context.initWorkspace(),/module/);assert.equal(broken.fetches,1);assert.equal(broken.installs,1);
});
