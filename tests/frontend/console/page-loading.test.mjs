import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const main = await readFile(new URL('../../../console/main.js', import.meta.url), 'utf8');
const loaderSource = await readFile(new URL('../../../console/page-loader.js', import.meta.url), 'utf8');
const { createPageLoader } = await import(`data:text/javascript;base64,${Buffer.from(loaderSource).toString('base64')}`);
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
function harness({allowed=true, authenticated=true, pending={}}={}) {
  const calls=[];
  const panels=['workspace','configuration','diagnostics'].map(name=>({dataset:{pagePanel:name},active:name==='workspace',classList:{toggle(_name,value){panels.find(p=>p.dataset.pagePanel===name).active=value;}}}));
  const location={hash:'#/configuration'};
  const window={DeepAgentConsoleAPI:{adminAccess:async()=>{calls.push('access');return {allowed,roles:allowed?['enterprise_admin']:[]};}},lucide:{createIcons(){}},addEventListener(){}};
  const modules={
    configuration:{async initConfiguration(){calls.push('init:configuration');await pending.configuration?.promise;},restoreConfigurationTab(){calls.push('refresh:configuration');},rememberConfigurationTab(){}},
    workspace:{async initWorkspace(){calls.push('init:workspace');await pending.workspace?.promise;}},
    'diagnostics-center':{initDiagnostics(){calls.push('init:diagnostics');},setDiagnosticsActive(active){calls.push(`diagnostics:${active}`);}},
  };
  const context=vm.createContext({window,location,console,createPageLoader,initUserIdentityControls(){},refreshIdentity:async()=>{calls.push('identity');return authenticated;},document:{body:{dataset:{},classList:{add(){},remove(){}}},getElementById(){return null;},querySelector(selector){return selector==='[data-page-panel].active'?panels.find(p=>p.active):null;},querySelectorAll(selector){return selector==='[data-page-panel]'?panels:[];}},history:{replaceState(_s,_t,hash){location.hash=hash;},pushState(_s,_t,hash){location.hash=hash;}},importPage:async(path)=>{const name=path.match(/\.\/([^/.]+)\.js/)[1];calls.push(`import:${name}`);return modules[name];}});
  const script=main.replace(/^import .*;\n/gm,'').replace(/await import\(/g,'await importPage(').split('\ninitConsoleSelects();')[0];
  vm.runInContext(script,context);
  for(const fn of ['bindNavigation','bindThemes','bindContextControls','loadProductVersion'])context[fn]=()=>{};
  return {context,calls,panels,window,modules};
}
test('page loader shares concurrent initialization and retries a failed load',async()=>{
  const gate=deferred();let count=0;const load=createPageLoader({one:()=>{count++;return gate.promise;}});
  const a=load('one'),b=load('one');assert.equal(a,b);await Promise.resolve();assert.equal(count,1);gate.resolve(42);assert.equal(await a,42);assert.equal(await load('one'),42);assert.equal(count,1);
  let attempts=0;const retry=createPageLoader({one:()=>{if(++attempts===1)throw Error('unavailable');return 8;}});await assert.rejects(retry('one'));assert.equal(await retry('one'),8);
});
test('configuration deep link loads only its own page after identity and admin access',async()=>{
 const h=harness();await h.context.boot();assert.deepEqual(h.calls,['identity','access','import:configuration','init:configuration','refresh:configuration']);assert.equal(h.panels.find(p=>p.active).dataset.pagePanel,'configuration');
});
test('unauthenticated or non-admin deep links never initialize any business page',async()=>{
 for(const options of [{authenticated:false},{allowed:false}]){const h=harness(options);await h.context.boot();assert.equal(h.calls.some(x=>x.startsWith('import:')),false);}
});
test('late configuration initialization does not restore a tab over a newer route',async()=>{
 const gate=deferred();const h=harness({pending:{configuration:gate}});const first=h.context.boot();while(!h.calls.includes('init:configuration'))await Promise.resolve();await h.context.showPage('diagnostics');gate.resolve();await first;assert.equal(h.panels.find(p=>p.active).dataset.pagePanel,'diagnostics');assert.equal(h.calls.includes('refresh:configuration'),false);assert.ok(h.calls.includes('diagnostics:true'));
});
test('each dirty configuration guard reports rejected navigation to cross-page callers',async()=>{
 for(const [name,guard] of [
  ['agentsAdmin',{isEditorOpen:()=>true,requestCloseEditor:async()=>false}],
  ['mcpAdmin',{isEditorOpen:()=>true,requestCloseEditor:async()=>false}],
  ['integrationsAdmin',{deactivate:()=>false}],
  ['wecomApplicationsAdmin',{deactivate:()=>false}],
 ]){
  const h=harness();await h.context.boot();h.window[name]=guard;
  assert.equal(await h.context.showPage('workspace'),false,name);
  assert.equal(h.calls.includes('import:workspace'),false);
  assert.equal(h.panels.find(p=>p.active).dataset.pagePanel,'configuration');
 }
});
test('repeated workspace navigation initializes once without cancelling existing execution',async()=>{
 const h=harness();await h.context.boot();let refreshes=0;h.window.DeepAgentConsoleChat={refreshConfiguration(){refreshes++;},abort(){throw Error('must not abort');}};await h.context.showPage('workspace');await h.context.showPage('configuration');await h.context.showPage('workspace');assert.equal(h.calls.filter(x=>x==='init:workspace').length,1);assert.equal(refreshes,1);
});
