import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import { escapeHtml, icon, selectHtml } from '../../../console/diagnostics-ui.js';
import { diagnosticHash } from '../../../console/diagnostics-navigation.js';
const source = await readFile(new URL('../../../console/diagnostics-usage.js', import.meta.url), 'utf8');
const filters = { period:'7', start:'2026-09-16T00:00:00Z', end:'2026-09-23T00:00:00Z', timezone:'UTC' };
const settle = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function harness(request, extra = {}) {
  const listeners = {}, requests = [];
  const nodes = new Map(['#usageDetailRows','#usageDetailFooter','#usageContributionRows','#usageContributionFooter','.usage-workbench','.usage-table-frame'].map(key => [key,{innerHTML:'',clientWidth:1000}]));
  const root = {set innerHTML(value) { this.html=value; for (const node of nodes.values()) { node.scrollTop=0; node.scrollLeft=0; } }, addEventListener(name, fn) { listeners[name] = fn; }, querySelector(selector) { return nodes.get(selector) || null; }};
  const context = vm.createContext({ Date, Intl, URLSearchParams, AbortController, setTimeout, clearTimeout,
    ResizeObserver: class {observe(){} disconnect(){}}, e:escapeHtml, icon, icons(){}, selectHtml, diagnosticHash,
    DIAGNOSTIC_PERIODS:[['168','最近 7 天']], diagnosticRangeHelpHtml:()=>'', bindDiagnosticRangeDismiss(){}, bindDiagnosticSelects(){},
    bindChannelResizer:()=>()=>{}, diagnosticTime:value=>value, window:{},
    diagnosticRequest(path,signal) { requests.push({path,signal}); return request(path,signal); }
  });
  vm.runInContext(source.replace(/^import .*;\n/gm,'').replace(/^export /gm,''),context);
  const controller=context.initDiagnosticUsage({root,navigate(){},replace(){}});
  const saved={filters,draft:filters,report:{},details:{items:[],total:21},...extra};
  controller.activate({filters},saved);
  return {controller,requests,nodes, event(selector,dataset) { return listeners.click({target:{closest(wanted) { return wanted===selector ? {dataset} : null; }}}); }, click(action) { return listeners.click({target:{closest(selector) { return selector==='[data-usage-action]' ? {dataset:{usageAction:action}} : null; }}}); }};
}

test('usage pagination ignores repeated clicks while pending and clamps a shrinking result', async()=>{
  const pending=deferred(); let count=0;
  const h=harness(()=> ++count===1 ? Promise.resolve({items:[],total:21}) : count===2 ? pending.promise : Promise.resolve({items:[],total:5}));
  await settle(); await h.click('next'); await h.click('next');
  assert.equal(h.requests.length,2);
  assert.match(h.requests[1].path,/offset=10/);
  pending.resolve({items:[],total:5}); await settle(); await settle();
  assert.equal(h.controller.snapshot().offset,0);
  assert.equal(h.controller.snapshot().detailBusy,false);
  assert.equal(h.requests.length,3);
});

test('usage snapshot resumes a contribution read interrupted by navigation',async()=>{
  const h=harness(path=>Promise.resolve(path.includes('/sessions?')?{items:[],total:0}:{items:[{key:'a',requests:1}],total:1}),
    {contributing:{dimension:'agent',key:'a',name:'A'},contributions:null});
  await settle(); await settle();
  assert.equal(h.requests.filter(row=>row.path.includes('/sessions?')).length,1);
  assert.notEqual(h.controller.snapshot().contributions,null);
});

test('usage load more allows only one request and appends without losing existing rows',async()=>{
  const pending=deferred();
  const h=harness(path=>path.includes('/sessions?')?pending.promise:Promise.resolve({items:[{key:'a',requests:1}],total:1}),
    {contributing:{dimension:'agent',key:'a',name:'A'},contributions:{items:[{session_id:'s1'}],total:2}});
  await settle(); h.nodes.get('#usageContributionRows').scrollTop=240; const first=h.click('contribution-more'); await h.click('contribution-more');
  assert.equal(h.requests.filter(row=>row.path.includes('/sessions?')).length,1);
  pending.resolve({items:[{session_id:'s2'}],total:2}); await first;
  assert.deepEqual(Array.from(h.controller.snapshot().contributions.items,row=>row.session_id),['s1','s2']);
  assert.equal(h.nodes.get('#usageContributionRows').scrollTop,240);
});


test('usage chart rerenders retain both pane scroll positions; changing dimension resets them',async()=>{
  const h=harness(()=>Promise.resolve({items:[],total:0}));
  await settle();
  h.nodes.get('.usage-table-frame').scrollTop=120;
  h.nodes.get('.usage-table-frame').scrollLeft=32;
  h.nodes.get('#usageContributionRows').scrollTop=240;
  await h.event('[data-usage-series]',{usageSeries:'a'});
  assert.equal(h.nodes.get('.usage-table-frame').scrollTop,120);
  assert.equal(h.nodes.get('.usage-table-frame').scrollLeft,32);
  assert.equal(h.nodes.get('#usageContributionRows').scrollTop,240);
  await h.event('[data-usage-dimension]',{usageDimension:'model'});
  await settle();
  assert.equal(h.nodes.get('.usage-table-frame').scrollTop,0);
  assert.equal(h.nodes.get('#usageContributionRows').scrollTop,0);
});


test('model usage distinguishes legacy and deleted records without leaking internal model keys', async () => {
  const items = [
    {key:'legacy:deepseek|deepseek-flash',model:{name:'deepseek-flash',provider_name:'deepseek'},requests:1,total_tokens:100},
    {key:'deleted-id',model:{name:'old-model',deleted:true},requests:1,total_tokens:20},
    {key:'legacy:|function:respond:',model:{name:'function:respond:'},requests:1,total_tokens:10},
    {key:'legacy:|',model:{name:null},requests:1,missing_requests:1,total_tokens:0},
  ];
  const h = harness(() => Promise.resolve({items,total:items.length,summary:{missing_requests:1}}), {dimension:'model'});
  await settle(); await settle();
  const html = h.nodes.get('#usageDetailRows').innerHTML;
  assert.match(html, /deepseek-flash<\/span><small class="usage-model-badge">历史/);
  assert.match(html, /old-model<\/span><small class="usage-model-badge">已删除/);
  assert.match(html, />测试模型<\/span>/);
  assert.match(html, />未记录模型<\/span>/);
  assert.match(html, /provider-logos\/deepseek.ico/);
  assert.match(h.nodes.get('#usageDetailFooter').innerHTML, /1 次用量不完整/);
});
