import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
const source = await readFile(new URL('../../../console/configuration/skills.js', import.meta.url), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const skill = (code, extra = {}) => ({ code, label: code, description: '查询与分析', source: 'upload', runtime_status: 'ready', resources: [], ...extra });
function harness(request = async () => []) {
  const nodes = new Map(), calls = [];
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, { innerHTML: '', scrollTop: 0, value: '', handlers: {},
      classList: { values: new Set(), toggle(name, force) { if (force) this.values.add(name); else this.values.delete(name); } },
      addEventListener(name, fn) { this.handlers[name] = fn; }, appendChild() {}, focus() {}, setSelectionRange() {},
      querySelector(selector) { return node(key + ' ' + selector); }, querySelectorAll() { return []; },
      matches() { return false; }, hidePopover() {},
    });
    return nodes.get(key);
  };
  const state = { skills: [] };
  const context = { state, AbortController, setTimeout, clearTimeout,
    window: { defaultApiBaseUrl: () => 'local', DeepAgentConsoleAPI: { loadContext: () => ({app: 'one'}) }, DeepAgentConsoleContext: { identity: { user: 'alice' } } },
    document: { activeElement: null, addEventListener() {}, getElementById: node,
      querySelector: key => node(key), querySelectorAll: () => [], createElement: () => node('created') },
    escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    formatDate: value => value || '—', showDbBanner() {}, showToast() {},
    apiFetch: (path, options) => { calls.push({ path, ...options }); return request(path, options); },
  };
  vm.runInNewContext(source, context);
  context.window.skillsAdmin.listLoaded = true;
  return { admin: context.window.skillsAdmin, state, node, calls, context };
}

test('引用按ID去重并与现有Skill取交集，同名Agent不混淆，筛选与搜索叠加', () => {
  const h = harness();
  h.state.skills = [skill('one'), skill('two'), skill('unlinked')];
  h.admin.agentReferences = [{ id: 'a', name: '同名助手', skills: ['one', 'one', 'missing'] },
    { id: 'b', name: '同名助手', skills: ['one', 'two'] }, { id: 'c', name: '失效引用', skills: ['missing'] }, { id: 'd', name: '空白', skills: [] }];
  h.admin.referencesState = 'ready'; h.admin.buildReferenceIndex();
  assert.deepEqual(Array.from(h.admin.filterableAgents(), a => a.id), ['a', 'b']);
  assert.equal(h.admin.referenceIndex.get('one').length, 2);
  h.admin.agentFilter = 'b'; assert.equal(h.admin.orderedReferences('one')[0].id, 'b');
  assert.deepEqual(Array.from(h.admin.visibleSkills(), s => s.code), ['one', 'two']);
  h.admin.query = 'TWO'; assert.deepEqual(Array.from(h.admin.visibleSkills(), s => s.code), ['two']);
  h.admin.agentFilter = 'unlinked'; h.admin.query = ''; assert.deepEqual(Array.from(h.admin.visibleSkills(), s => s.code), ['unlinked']);
});

test('表格单列引用、+N浮层入口与运行状态列；名称不含状态点，保留三个资源数量并转义', () => {
  const h = harness(); h.state.skills = [skill('<one>', { label: '<测试>', runtime_status: undefined, reference_count: 2, asset_count: 1, script_count: 3 })];
  h.admin.agentReferences = [{id:'a',name:'A',skills:['<one>']},{id:'b',name:'<B>',skills:['<one>']}];
  h.admin.referencesState = 'ready'; h.admin.buildReferenceIndex(); h.admin.renderList();
  const html = h.node('skillListPane').innerHTML;
  assert.ok(html.includes('筛选 Agent') && html.includes('未被引用'));
  assert.ok(!html.includes('全部来源') && !html.includes('skill-runtime-dot'));
  assert.ok(html.includes('&lt;测试>') && !html.includes('<测试>'));
  assert.ok(html.includes('skill-status-tag runtime-missing') && html.includes('未加载'));
  assert.ok(html.includes('>+1</button>') && !html.includes('>&lt;B></span>'));
  for (const title of ['References', 'Assets', '脚本']) assert.ok(html.includes(`title="${title}"`));
  assert.ok(!html.includes('data-filter-agent'),'引用名称只展示，不增加筛选热区');
  assert.ok(html.includes('<span class="skill-ref-chip"'));
});

test('+N点击只展开引用浮层并阻止行导航，普通行点击和键盘仍进入详情', () => {
  const h=harness();h.state.skills=[skill('one')];h.admin.referencesState='ready';
  const pane=h.node('skillListPane'),row=h.node('row'),more=h.node('more');
  row.dataset={skillRow:'one'};more.dataset={skillRefs:'one'};
  pane.querySelectorAll=selector=>selector==='[data-skill-row]'?[row]:selector==='[data-skill-refs]'?[more]:[];
  const opened=[],shown=[];let stopped=0;
  h.admin.openDetail=code=>opened.push(code);h.admin.showReferences=(code,anchor)=>shown.push([code,anchor]);
  h.admin.renderList();
  more.handlers.click({stopPropagation(){stopped+=1;}});
  assert.equal(stopped,1);assert.equal(opened.length,0);assert.equal(shown[0][0],'one');assert.equal(shown[0][1],more);
  assert.equal(h.admin.agentFilter,'all');
  row.handlers.click({target:{closest:()=>more}});assert.equal(opened.length,0);
  row.handlers.click({target:{closest:selector=>selector.includes('.skill-ref-chips')?h.node('reference-label'):null}});
  assert.equal(opened.length,0,'引用标签及间隙点击无操作，不进入详情或筛选');
  row.handlers.click({target:{closest:()=>null}});assert.deepEqual(opened,['one']);
  row.handlers.keydown({target:row,key:'Enter',preventDefault(){}});assert.deepEqual(opened,['one','one']);
});

test('引用浮层仅展示其余Agent，不提供点击筛选操作', () => {
  const h=harness();h.state.skills=[skill('one')];
  h.admin.agentReferences=[{id:'a',name:'A',skills:['one']},{id:'b',name:'B',skills:['one']},{id:'c',name:'C',skills:['one']}];
  h.admin.buildReferenceIndex();
  const popover=h.node('skillAgentRefsPopover');popover.dataset={};popover.style={};popover.showPopover=()=>{};popover.getBoundingClientRect=()=>({width:240,height:80});
  h.context.window.innerWidth=1437;h.context.window.innerHeight=1344;
  const anchor={setAttribute(){},getBoundingClientRect:()=>({left:600,top:500,bottom:520})};
  h.admin.showReferences('one',anchor);
  assert.ok(popover.innerHTML.includes('>B</span>') && popover.innerHTML.includes('>C</span>'));
  assert.ok(!popover.innerHTML.includes('<button') && !popover.innerHTML.includes('data-agent-id'));
  assert.equal(h.admin.agentFilter,'all');
});

test('引用失败不伪装未引用，Agent筛选禁用，但关键词搜索仍然可用', async () => {
  const h = harness(async () => { throw new Error('references unavailable'); });
  h.state.skills = [skill('one')]; h.admin.listLoaded = true; h.admin.agentFilter = 'unlinked';
  await h.admin.refreshReferences();
  const html = h.node('skillListPane').innerHTML;
  assert.equal(h.admin.referencesState, 'error');
  assert.ok(html.includes('获取失败') && html.includes('重试引用查询') && html.includes('disabled'));
  assert.ok(!html.includes('class="muted">未被引用'));
  assert.equal(h.admin.visibleSkills().length, 1);
  h.admin.query = 'absent'; assert.equal(h.admin.visibleSkills().length, 0);
});

test('进入列表并行读取两个轻量接口，不自动读取任意详情、资源或完整Agent', async () => {
  const a = deferred(), b = deferred();
  const h = harness(path => path.endsWith('references/agents') ? a.promise : b.promise);
  const pending = h.admin.refresh();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls.map(x => x.path).sort(), ['/api/v1/skills', '/api/v1/skills/references/agents']);
  b.resolve([skill('one')]); a.resolve([{id:'a',name:'A',skills:['one']}]); await pending;
  assert.equal(h.admin.view, 'list'); assert.equal(h.admin.selectedCode, null);
  assert.equal(h.calls.length, 2); assert.equal(h.admin.readRequests.size, 0);
});

test('重叠刷新、上下文变化与引用重试均忽略迟到数据，取消旧读请求', async () => {
  const first = deferred(), next = deferred(); let index = 0;
  const h = harness(() => (++index === 1 ? first.promise : next.promise));
  const p = h.admin.refreshReferences(), q = h.admin.refreshReferences();
  assert.equal(h.calls[0].signal.aborted, true);
  next.resolve([{id:'new',name:'New',skills:[]}]); await q;
  first.resolve([{id:'old',name:'Old',skills:[]}]); await p;
  assert.equal(h.admin.agentReferences[0].id, 'new');
  const late = deferred(); h.context.apiFetch = () => late.promise;
  const r = h.admin.refreshReferences();
  h.context.window.DeepAgentConsoleAPI.loadContext = () => ({app:'two'});
  late.resolve([{id:'wrong-context',name:'Wrong',skills:[]}]); await r;
  assert.equal(h.admin.agentReferences[0].id, 'new'); assert.equal(h.admin.readRequests.size, 0);
});

test('切换详情和返回后旧详情不能覆盖当前页面；详情与文件请求不跨选择污染', async () => {
  const a = deferred(), b = deferred();
  const h = harness(path => path.endsWith('/a') ? a.promise : b.promise);
  // Keep the real select/cancellation lifecycle and observe the committed detail.
  const rendered = []; h.admin.renderDetail = value => rendered.push(value.code);
  const p = h.admin.select('a'), q = h.admin.select('b');
  assert.equal(h.calls[0].signal.aborted, true);
  b.resolve(skill('b')); await q; a.resolve(skill('a')); await p;
  assert.deepEqual(rendered, ['b']);
  const c = deferred(); h.context.apiFetch = () => c.promise;
  const r = h.admin.select('c'); h.admin.backToList(); c.resolve(skill('c')); await r;
  assert.deepEqual(rendered, ['b']); assert.equal(h.admin.view, 'list');
});

test('资源预览连续切换及离页取消后迟到内容不覆盖新的文件', async () => {
  const a = deferred(), b = deferred(); let count=0;
  const h = harness(() => (++count === 1 ? a.promise : b.promise));
  h.admin.selectedCode = 'one'; h.admin.view = 'detail';
  const p = h.admin.readResource('one', 'a.txt'), q = h.admin.readResource('one', 'b.txt');
  b.resolve({content:'current-content',format:'text'}); await q;
  a.resolve({content:'stale-content',format:'text'}); await p;
  assert.ok(h.node('skillResourcePreview').innerHTML.includes('current-content'));
  assert.ok(!h.node('skillResourcePreview').innerHTML.includes('stale-content'));
  const c=deferred(); h.context.apiFetch=()=>c.promise;
  const r=h.admin.readResource('one','c.txt'); h.admin.deactivate(); c.resolve({content:'after-leaving'}); await r;
  assert.ok(!h.node('skillResourcePreview').innerHTML.includes('after-leaving'));
  assert.equal(h.admin.readRequests.size,0);
});

test('引用维护刷新后失效Agent回到全部，列表筛选结果不再使用旧关联', async () => {
  const h=harness(async()=>[{id:'b',name:'Renamed',skills:['one']}]);
  h.state.skills=[skill('one')];h.admin.agentFilter='a';h.admin.listLoaded=true;
  await h.admin.refreshReferences();
  assert.equal(h.admin.agentFilter,'all');
  assert.equal(h.admin.referenceIndex.get('one')[0].name,'Renamed');
});


test('详情刷新失败后返回保留错误与重试，权限拒绝清空旧列表而不重新展示缓存', async () => {
  const forbidden = Object.assign(new Error('HTTP 403'), {status:403});
  const h = harness(async path => { if (path.endsWith('references/agents')) return []; throw forbidden; });
  h.state.skills = [skill('old-private-skill')]; h.admin.view = 'detail'; h.admin.selectedCode = 'old-private-skill';
  await h.admin.refresh();
  assert.equal(h.admin.listLoaded, false);
  h.admin.backToList();
  assert.ok(h.node('skillListPane').innerHTML.includes('HTTP 403'));
  assert.ok(h.node('skillListPane').innerHTML.includes('data-skill-retry'));
  assert.ok(!h.node('skillListPane').innerHTML.includes('old-private-skill'));
  assert.equal(h.state.skills.length,0);
  const unavailable = harness(async () => {throw new Error('HTTP 503');});
  unavailable.state.skills=[skill('cached')]; await unavailable.admin.refresh(); unavailable.admin.backToList();
  assert.ok(unavailable.node('skillListPane').innerHTML.includes('HTTP 503'));
  assert.ok(!unavailable.node('skillListPane').innerHTML.includes('data-skill-row'));
});


test('删除A等待期间用户已打开B，迟到的删除回执不能关闭B详情', async () => {
  const deleted=deferred(); const h=harness();
  h.state.skills=[skill('a'),skill('b')]; h.admin.selectedCode='a'; h.admin.view='detail';
  h.context.confirmAction=async()=>true;
  h.context.apiFetch=async (path,options={})=>{
    if(options.method==='DELETE') return deleted.promise;
    if(path.endsWith('references/agents')) return [];
    if(path==='/api/v1/skills') return [skill('b')];
    return skill('b');
  };
  const rendered=[];h.admin.renderDetail=value=>rendered.push(value.code);
  const removing=h.admin.handleAction('delete',skill('a')); await Promise.resolve();
  h.admin.selectedCode='b';h.admin.view='detail';deleted.resolve({});await removing;
  assert.equal(h.admin.view,'detail');assert.equal(h.admin.selectedCode,'b');assert.deepEqual(rendered,['b']);
});


test('引用还在读取时用户打开详情，引用完成不额外重复读取当前详情', async () => {
  const refs=deferred();const h=harness(async path=>path.endsWith('references/agents')?refs.promise:path==='/api/v1/skills'?[skill('one')]:skill('one'));
  const rendered=[];h.admin.renderDetail=value=>rendered.push(value.code);
  const refreshing=h.admin.refresh();await Promise.resolve();await Promise.resolve();
  await h.admin.select('one');refs.resolve([]);await refreshing;
  assert.equal(h.calls.filter(call=>call.path==='/api/v1/skills/one').length,1);
  assert.deepEqual(rendered,['one']);
});

test('进入详情后返回恢复列表滚动、筛选和查询，搜索及Agent筛选回到列表顶部', () => {
  const h=harness();h.state.skills=[skill('one')];h.admin.referencesState='ready';h.admin.agentReferences=[{id:'a',name:'A',skills:['one']}];h.admin.buildReferenceIndex();
  h.admin.agentFilter='a';h.admin.query='one';h.node('#skillListPane .skill-table-wrap').scrollTop=345;
  h.admin.select=code=>{h.admin.selectedCode=code;h.admin.view='detail';};
  h.admin.openDetail('one');h.admin.backToList();
  assert.equal(h.admin.agentFilter,'a');assert.equal(h.admin.query,'one');
  assert.equal(h.node('skillListPane .skill-table-wrap').scrollTop,345);
  h.admin.filterByAgent('all');assert.equal(h.node('skillListPane .skill-table-wrap').scrollTop,0);
});


test('每个筛选自定义在前内置在后，保持各组原顺序且不修改服务端目录', () => {
  const h=harness();
  h.state.skills=[skill('builtin-a',{source:'builtin'}),skill('custom-b'),skill('builtin-c',{source:'builtin'}),skill('custom-d')];
  h.admin.referencesState='ready';h.admin.agentReferences=[{id:'a',name:'A',skills:['builtin-a','custom-b']}];h.admin.buildReferenceIndex();
  assert.deepEqual(Array.from(h.admin.visibleSkills(),s=>s.code),['custom-b','custom-d','builtin-a','builtin-c']);
  assert.deepEqual(h.state.skills.map(s=>s.code),['builtin-a','custom-b','builtin-c','custom-d']);
  h.admin.agentFilter='a';assert.deepEqual(Array.from(h.admin.visibleSkills(),s=>s.code),['custom-b','builtin-a']);
  h.admin.agentFilter='unlinked';assert.deepEqual(Array.from(h.admin.visibleSkills(),s=>s.code),['custom-d','builtin-c']);
  h.admin.agentFilter='all';h.admin.renderList();const html=h.node('skillListPane').innerHTML;
  assert.ok(html.includes('全部 Skill') && !html.includes('全部 Agent'));
  assert.ok(html.includes('data-icon="library"') && html.includes('data-icon="unlink"') && html.includes('data-icon="bot"'));
  assert.equal((html.match(/data-separator-before="true"/g)||[]).length,1);
  assert.ok(!html.includes('>自定义<'));
  assert.ok(html.includes('class="skill-unreferenced"'));
});


test('引用读取等待和失败回退全部时，下拉显示与表格口径保持一致', async () => {
  const pending=deferred();const h=harness(()=>pending.promise);
  h.state.skills=[skill('linked'),skill('unlinked')];h.admin.agentReferences=[{id:'a',name:'A',skills:['linked']}];
  h.admin.referencesState='ready';h.admin.agentFilter='a';h.admin.buildReferenceIndex();
  const reading=h.admin.refreshReferences();
  assert.equal(h.admin.agentFilter,'a','等待成功时保留原筛选用于恢复');
  assert.ok(h.node('skillListPane').innerHTML.includes('value="all" data-icon="library" selected'));
  assert.equal(h.admin.visibleSkills().length,2);
  pending.reject(new Error('unavailable'));await reading;
  assert.equal(h.admin.agentFilter,'all');
  assert.ok(h.node('skillListPane').innerHTML.includes('value="all" data-icon="library" selected'));
  assert.equal(h.admin.visibleSkills().length,2);
});

test('中文输入法组词期间不重建搜索框，确认文字后再筛选', () => {
  const h=harness();h.state.skills=[skill('one',{label:'电子表格'})];h.admin.renderList();
  const input=h.node('skillListPane #skillSearch');
  const render=h.admin.renderList.bind(h.admin);let renders=0;
  h.admin.renderList=options=>{renders+=1;render(options);};
  input.handlers.compositionstart?.();
  input.value='dian';input.handlers.input({target:input,isComposing:true});
  assert.equal(renders,0,'组词过程不能替换正在输入的DOM');
  input.value='电子';input.handlers.compositionend({target:input});
  assert.equal(renders,1);assert.equal(h.admin.query,'电子');
  assert.equal(h.admin.visibleSkills().length,1);
  const next=h.node('skillListPane #skillSearch');next.value='absent';next.handlers.input({target:next,isComposing:false});
  assert.equal(h.admin.visibleSkills().length,0);
});

test('所有文件都可选中，代码、图片、Office与占位按实际类型区分', () => {
  const h=harness();
  const resources=[
    {path:'scripts/run.py',category:'script',read_format:'text'},
    {path:'assets/chart.png',category:'asset'},
    {path:'references/report.pdf',category:'reference',read_format:'text',readable:true},
    {path:'assets/slides.pptx',category:'asset',read_format:'text',readable:true},
    {path:'assets/template.docx',category:'asset',read_format:'docx'},
    {path:'scripts/template.xlsx',category:'script',read_format:'xlsx'},
  ];
  assert.deepEqual(resources.map(r=>h.admin.previewMode(r)),['code','image','unsupported','unsupported','text','text']);
  h.admin.view='detail';h.admin.selectedCode='one';h.admin.selectedResourcePath='references/report.pdf';
  h.admin.readResource=()=>{};h.admin.renderDetail(skill('one',{resources}));
  const html=h.node('skillDetailPane').innerHTML;
  for(const r of resources) assert.ok(html.includes(`data-read-resource="${r.path}"`));
  assert.ok(html.includes('data-resource-path="references/report.pdf"') && html.includes(' active'));
  assert.ok(!html.includes('脚本不披露源码'));
});

for (const path of ['scripts/constructor', 'scripts/file.__proto__']) {
  test(`合法未知资源 ${path} 使用通用图标且详情不崩溃`, () => {
    const h=harness();
    const resource={path,category:'script',read_format:null};
    const icon=h.admin.resourceIcon(resource);
    assert.equal(icon.icon,'file-code-2');assert.equal(icon.type,'code');
    h.admin.view='detail';h.admin.selectedCode='one';h.admin.readResource=()=>{};
    h.admin.renderDetail(skill('one',{resources:[resource]}));
    const html=h.node('skillDetailPane').innerHTML;
    assert.ok(html.includes(`data-read-resource="${path}"`));
    assert.ok(html.includes('data-lucide="file-code-2" data-file-type="code"'));
  });
}

test('未知脚本后缀不命中对象继承属性，不进入语法高亮器并保留转义原文', () => {
  const h=harness(),calls=[];
  h.context.window.hljs={getLanguage(){assert.fail('未知语言不应调用高亮器');}};
  h.context.window.DeepAgentMarkdown={renderCodeBlock:(html,language,options)=>{calls.push({html,language,options});return html;}};
  const content='<script>must stay text</script>\nunknown source';
  for(const path of ['scripts/file.constructor','scripts/file.__proto__']) {
    const html=h.admin.renderCodePreview(content,path);
    assert.ok(html.includes('&lt;script>') && !html.includes('<script>'));
    assert.equal(calls.at(-1).language,'');assert.equal(calls.at(-1).options.source,content);
  }
});

test('源码使用公共代码块并转义HTML；已知语言高亮失败仍显示完整原文', () => {
  const h=harness();const calls=[];
  h.context.window.DeepAgentMarkdown={renderCodeBlock:(html,language,options)=>{calls.push({html,language,options});return `<pre><code>${html}</code></pre>`;}};
  const source='print("</code><script>bad()</script>")\n中文🙂';
  const html=h.admin.renderCodePreview(source,'scripts/REPORT.PY');
  assert.equal(calls[0].language,'python');assert.equal(calls[0].options.source,source);assert.equal(calls[0].options.expanded,true);
  assert.ok(html.includes('&lt;script>') && !html.includes('<script>'));
  h.context.window.hljs={getLanguage:()=>true,highlight(){throw new Error('unsupported');}};
  assert.ok(h.admin.renderCodePreview(source,'run.py').includes('中文🙂'));
});

test('PDF与PPTX点击显示下载占位，不请求二进制为文本，下载仍走现有Skill包入口', async () => {
  const h=harness();h.admin.selectedCode='one';h.admin.view='detail';
  const opened=[];h.admin.handleAction=(action,item)=>opened.push([action,item.code]);
  for(const path of ['assets/report.pdf','assets/slides.pptx']){
    await h.admin.readResource('one',path);
    const html=h.node('skillResourcePreview').innerHTML;
    assert.ok(html.includes('暂不支持在线预览') && html.includes('下载 Skill 包'));
    assert.equal(h.calls.length,0);assert.equal(h.admin.readRequests.size,0);
    h.node('skillResourcePreview [data-preview-download]').handlers.click();
  }
  assert.deepEqual(opened,[['download','one'],['download','one']]);
});

test('代码下一段保留服务端双游标，返回开头不夹带旧范围', async () => {
  const h=harness(path=>Promise.resolve(path.includes('?')?{format:'text',content:'second',has_more:false}:{format:'text',content:'first',has_more:true,next_offset:501,next_char_offset:17}));
  h.admin.selectedCode='one';h.admin.view='detail';
  h.admin.details.set('one',skill('one',{resources:[{path:'scripts/run.py',category:'script',read_format:'text'}]}));
  await h.admin.readResource('one','scripts/run.py');
  await h.node('skillResourcePreview [data-preview-next]').handlers.click();
  assert.ok(h.calls[1].path.endsWith('?offset=501&char_offset=17'));
  assert.ok(h.node('skillResourcePreview').innerHTML.includes('second'));
  await h.node('skillResourcePreview [data-preview-start]').handlers.click();
  assert.equal(h.calls[2].path,'/api/v1/skills/one/resources/scripts/run.py');
});

test('图片经鉴权fetch读取，迟到图片不创建URL，切文件和离页回收URL', async () => {
  const h=harness(async()=>({content:'text',format:'text'}));h.admin.selectedCode='one';h.admin.view='detail';
  const a=deferred(),b=deferred(),requested=[],created=[],revoked=[];
  h.context.serviceUrl=path=>'/prefix'+path;
  h.context.daFetch=async(path,options)=>{requested.push({path,options});return{ok:true,blob:()=>path.includes('a.png')?a.promise:b.promise};};
  h.context.URL={createObjectURL(blob){created.push(blob);return'blob:current';},revokeObjectURL(url){revoked.push(url);}};
  const first=h.admin.readResource('one','assets/a.png'),second=h.admin.readResource('one','assets/b.png');
  b.resolve({name:'b'});await second;a.resolve({name:'a'});await first;
  assert.equal(requested[0].path,'/prefix/api/v1/skills/one/resources/assets/a.png?raw=true');
  assert.equal(requested[0].options.signal.aborted,true);assert.deepEqual(created,[{name:'b'}]);
  assert.equal(h.node('skillResourcePreview img').src,'blob:current');
  await h.admin.readResource('one','notes.txt');assert.deepEqual(revoked,['blob:current']);
  h.admin.previewObjectUrl='blob:again';h.admin.deactivate();assert.deepEqual(revoked,['blob:current','blob:again']);
});

test('图片请求失败显示明确失败，不制造图片URL或成功占位', async () => {
  const h=harness();h.admin.selectedCode='one';h.admin.view='detail';
  h.context.serviceUrl=x=>x;h.context.daFetch=async()=>({ok:false,status:403});
  await h.admin.readResource('one','assets/denied.svg');
  assert.ok(h.node('skillResourcePreview').innerHTML.includes('图片读取失败（HTTP 403）'));
  assert.equal(h.admin.previewObjectUrl,null);assert.equal(h.admin.readRequests.size,0);
});
