import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import { escapeHtml, icon, selectHtml, diagnosticRequestOptions } from '../../../console/diagnostics-ui.js';
import { diagnosticTarget, sameDiagnosticFilters } from '../../../console/diagnostics-navigation.js';
import { usageAveragePresentation } from '../../../console/diagnostics-usage.js';
const source = await readFile(new URL('../../../console/diagnostics-usage.js', import.meta.url), 'utf8');
const filters = { period:'7', start:'2026-09-16T00:00:00Z', end:'2026-09-23T00:00:00Z', timezone:'UTC' };
const settle = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function harness(request, extra = {}, routeFilters = filters) {
  const listeners = {}, requests = [], replacements = [];
  const nodes = new Map(['#usageDetailRows','#usageDetailFooter','#usageContributionRows','#usageContributionFooter','.usage-workbench','.usage-table-frame'].map(key => [key,{innerHTML:'',clientWidth:1000}]));
  const root = {set innerHTML(value) { this.html=value; for (const node of nodes.values()) { node.scrollTop=0; node.scrollLeft=0; } }, addEventListener(name, fn) { listeners[name] = fn; }, querySelector(selector) { return nodes.get(selector) || null; }};
  const context = vm.createContext({ Date, Intl, URLSearchParams, AbortController, setTimeout, clearTimeout,
    ResizeObserver: class {observe(){} disconnect(){}}, e:escapeHtml, icon, icons(){}, selectHtml, diagnosticTarget, sameDiagnosticFilters, diagnosticRequestOptions,
    DIAGNOSTIC_PERIODS:[['168','最近 7 天']], diagnosticRangeHelpHtml:()=>'', bindDiagnosticRangeDismiss(){}, bindDiagnosticSelects(){},
    bindChannelResizer:()=>()=>{}, diagnosticTime:value=>value, window:{},
    diagnosticRequest(path,signal) { requests.push({path,signal}); return request(path,signal); }
  });
  vm.runInContext(source.replace(/^import .*;\n/gm,'').replace(/^export /gm,''),context);
  const controller=context.initDiagnosticUsage({root,navigate(){},replace(target){replacements.push(target);}});
  const saved={filters,draft:filters,report:{},details:{items:[],total:21},...extra};
  controller.activate({filters:routeFilters},saved);
  return {controller,requests,replacements,nodes,root, dispatch:event=>listeners.click(event), event(selector,dataset) { return listeners.click({target:{closest(wanted) { return wanted===selector ? {dataset} : null; }}}); }, click(action) { return listeners.click({target:{closest(selector) { return selector==='[data-usage-action]' ? {dataset:{usageAction:action}} : null; }}}); }};
}

test('usage average uses all complete records and preserves explicitly reported zero', () => {
  assert.deepEqual(usageAveragePresentation({requests:2,total_tokens:3000,missing_requests:0}),
    {value:'1.5K',partial:false,hint:'合计 Token / 模型请求次数。'});
  assert.equal(usageAveragePresentation({requests:1,total_tokens:0,missing_requests:0}).value,'0');
});

test('usage average fallback excludes both missing and partially measured calls', () => {
  // Complete call: 100 + 20; partial call: 9000 + unknown; unreported call.
  const row={requests:3,total_tokens:9120,input_reported_requests:2,output_reported_requests:1,
    missing_requests:2,complete_requests:1,complete_tokens:120};
  assert.deepEqual(usageAveragePresentation(row),
    {value:'120',partial:true,hint:'按完整记录 1/3 次计算，2 次用量缺失未计入。'});
  assert.equal(usageAveragePresentation({...row,complete_tokens:0}).value,'0');
});

test('usage average explains no requests and no fully measured requests without inventing zero', () => {
  assert.deepEqual(usageAveragePresentation({requests:0,total_tokens:0,missing_requests:0}),
    {value:'—',partial:false,hint:'当前范围没有模型请求。'});
  const average=usageAveragePresentation({requests:2,missing_requests:2,total_tokens:9000,
    complete_requests:0,complete_tokens:0});
  assert.equal(average.value,'—');
  assert.match(average.hint,/共 2 次请求，均缺少完整 Token 数据/);
});

test('older usage payload fallback requires proof that missing requests contributed neither token counter', () => {
  const row={requests:3,missing_requests:1,total_tokens:4000,input_reported_requests:2,output_reported_requests:2};
  assert.deepEqual(usageAveragePresentation(row),
    {value:'2.0K',partial:true,hint:'按完整记录 2/3 次计算，1 次用量缺失未计入。'});
  assert.equal(usageAveragePresentation({...row,input_reported_requests:3}).value,'—');
  assert.equal(usageAveragePresentation({requests:3,missing_requests:1,total_tokens:4000}).value,'—');
  assert.equal(usageAveragePresentation({...row,complete_requests:2}).value,'—');
});

test('summary and detail average show the same fallback value, visible marker and coverage explanation', async () => {
  const summary={requests:3,missing_requests:2,total_tokens:9120,
    input_reported_requests:2,output_reported_requests:1,complete_requests:1,complete_tokens:120};
  const items=[{key:'a',name:'Agent fixture',...summary}];
  const h=harness(()=>Promise.resolve({items,total:1,summary}),{report:{summary}});
  await settle();
  const detail=h.nodes.get('#usageDetailRows').innerHTML;
  for(const html of [h.root.html,detail]) {
    assert.match(html,/按完整记录 1\/3 次计算，2 次用量缺失未计入/);
    assert.match(html,/aria-label="120，按完整记录 1\/3 次计算，2 次用量缺失未计入。"/);
    assert.match(html,/>120\*<\/span>/);
    assert.doesNotMatch(html,/>3\.0K/);
  }
});

test('multi-channel contribution icons deduplicate and cycle without new requests', async () => {
  const items = [{key:'a',name:'A',channel_types:['feishu','wecom','feishu'],requests:1}];
  const h = harness(() => Promise.resolve({items,total:1}), {
    contributing:{dimension:'agent',key:'a',name:'A'}, contributions:{items:[],total:0},
  });
  await settle();
  const html = h.nodes.get('#usageContributionRows').innerHTML;
  assert.equal((html.match(/class="usage-icon-layer"/g) || []).length, 2);
  assert.match(html, /type="button"[^>]*data-usage-icon-cycle/);
  const layers = ['飞书','企业微信'].map((title,index)=>({title,dataset:new Proxy({depth:String(index)}, {
    set(target,key,value) { target[key]=String(value); return true; },
  })}));
  const attrs = {};
  const stack = {querySelectorAll:()=>layers,setAttribute:(name,value)=>{attrs[name]=value;}};
  const event = {target:{closest:selector=>selector==='[data-usage-icon-cycle]'?stack:null}};
  const requests = h.requests.length;
  await h.dispatch(event);
  assert.deepEqual(layers.map(layer=>layer.dataset.depth), ['1','0']);
  assert.equal(attrs['aria-label'], '当前企业微信，点击切换渠道图标');
  await h.dispatch(event);
  assert.deepEqual(layers.map(layer=>layer.dataset.depth), ['0','1']);
  assert.equal(attrs['aria-label'], '当前飞书，点击切换渠道图标');
  assert.equal(h.requests.length, requests);
});

test('single-channel contribution retains a noninteractive identity icon', async () => {
  const items = [{key:'a',name:'A',channel_types:['feishu'],requests:1}];
  const h = harness(() => Promise.resolve({items,total:1}), {
    contributing:{dimension:'agent',key:'a',name:'A'}, contributions:{items:[],total:0},
  });
  await settle();
  const html = h.nodes.get('#usageContributionRows').innerHTML;
  assert.match(html, /class="usage-object-icon"><img/);
  assert.doesNotMatch(html, /data-usage-icon-cycle/);
});

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
  assert.equal(h.nodes.get('#usageDetailFooter').innerHTML, '');
});


test('Agent contribution heading abbreviates ID but retains full copy value', async () => {
  const id = 'd0a8eb06-dcbc-493e-8157-254aa29b2ac5';
  const h = harness(() => Promise.resolve({items:[{key:id,name:'Agent fixture',requests:1}],total:1}), {
    contributing:{dimension:'agent',key:id,name:'Agent fixture'},
    contributions:{items:[],total:0},
  });
  await settle();
  const html = h.nodes.get('#usageContributionRows').innerHTML;
  assert.match(html, />d0a8eb06<\/code>/);
  assert.ok(html.includes(`data-usage-copy="${id}"`));
  assert.match(html, /aria-label="复制 Agent ID"/);
  assert.doesNotMatch(html, /Agent ·/);
});

test('usage restoration compares actual conditions rather than equal clean URLs', async () => {
  const h = harness(() => new Promise(() => {}), { report: { sentinel: 'stale report' } }, { ...filters, agent_id: 'other-agent' });
  assert.equal(h.controller.snapshot().report, null);
  assert.equal(h.controller.snapshot().filters.agent_id, 'other-agent');
  assert.equal(h.replacements.at(-1).hash, '#/diagnostics/usage');
  assert.equal(h.replacements.at(-1).filters.agent_id, 'other-agent');
  assert.ok(h.requests.some(item => item.path.startsWith('/api/v1/diagnostics/usage?')));
  h.controller.deactivate();
});

test('initial usage bounds publish only to page memory and remain restorable on Back', () => {
  const h = harness(() => new Promise(() => {}), {}, {});
  const snapshot = h.controller.snapshot(), target = h.replacements.at(-1);
  assert.equal(target.hash, '#/diagnostics/usage');
  assert.equal(target.filters.end, snapshot.filters.end);
  h.controller.deactivate();
  h.controller.activate({filters:target.filters}, {...snapshot, report:{ sentinel:'restored' }});
  assert.equal(h.controller.snapshot().report.sentinel, 'restored');
  h.controller.deactivate();
});
