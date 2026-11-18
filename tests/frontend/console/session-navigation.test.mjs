import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { readSse } from '../../../console/protocol.js';
const source = readFileSync(new URL('../../../console/chat.js', import.meta.url), 'utf8');
const part = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const tick = () => new Promise(setImmediate);

function refreshHarness({ status = 200, sessionId = 'old', streaming = false } = {}) {
  const state = { agentId: 'agent', sessionId, agents: [{ id: 'agent' }], sessions: [{id:'newest',agentId:'agent'}], abortController: streaming ? {} : null };
  const calls = [];
  const h = vm.createContext({ state, calls, el: { chatTitle: { textContent: 'old title' } },
    loadAgents: async () => {}, syncSessionsFromBackend: async () => {}, refreshSessionFilterOptions: async () => {},
    sessionApiUrl: s => s.id, sessionLookupKey: s => `${s.agentId}:${s.id}`,
    daFetch: async id => { calls.push(['get',id]); return {status,ok:status===200,json:async()=>({session_id:id})}; },
    mergeSessionsFromBackend() {},
    newChat: () => {state.sessionId=null; calls.push(['new']);},
    switchSession: async s => {state.sessionId=s.id; calls.push(['switch',s.id]);},
    showConsoleToast: message => calls.push(['error',message]),
  });
  vm.runInContext(part('async function refreshAgentWorkspace()', '\nwindow.DeepAgentConsoleChat'),h);
  return h;
}
test('refresh keeps a new conversation and does not select the newest item',async()=>{
  const h=refreshHarness({sessionId:null}); await h.refreshAgentWorkspace();
  assert.equal(h.state.sessionId,null); assert.deepEqual(h.calls,[]);
});
test('refresh keeps an older selection even outside the loaded/search page',async()=>{
  const h=refreshHarness(); await h.refreshAgentWorkspace();
  assert.equal(h.state.sessionId,'old'); assert.deepEqual(h.calls,[['get','old'],['switch','old']]);
});
test('only a confirmed missing session becomes a new conversation',async()=>{
  for(const status of [404,403,503]){
    const h=refreshHarness({status}); await h.refreshAgentWorkspace();
    assert.equal(h.state.sessionId,status===404?null:'old');
    assert.equal(h.calls.some(c=>c[0]==='switch'),false);
  }
});
test('refresh does not replace an active POST stream',async()=>{
  const h=refreshHarness({streaming:true}); await h.refreshAgentWorkspace();
  assert.equal(h.calls.some(c=>c[0]==='switch'),false);
});
test('manual refresh preserves observations, pending confirmations, and history loads',async()=>{
  for (const flag of ['observingRun', 'awaitingHitl', 'historyLoading']) {
    const h=refreshHarness(); h.state[flag]=true;
    await h.refreshAgentWorkspace();
    assert.equal(h.state.sessionId,'old');
    assert.equal(h.calls.some(c=>c[0]==='switch'||c[0]==='new'),false);
  }
  const h=refreshHarness({status:404,streaming:true});
  await h.refreshAgentWorkspace();
  assert.equal(h.state.sessionId,'old');
});
test('late refresh cannot jump back after user navigation',async()=>{
  const h=refreshHarness(); let finish;
  h.daFetch=()=>new Promise(resolve=>{finish=resolve;});
  const task=h.refreshAgentWorkspace(); await tick();
  h.state.sessionId='other'; h.state.navigationRevision=1;
  finish({ok:true,status:200}); await task;
  assert.equal(h.state.sessionId,'other'); assert.deepEqual(h.calls,[]);
});

function streamHarness() {
  const state={agentId:'agent',sessionId:'old',streaming:true}; const frames=[]; const calls=[];
  const sockets=[]; const noop=()=>{};
  const rows = ['old', 'new'].map(id => {
    const indicator = { hidden: true }, remove = { hidden: false };
    return { dataset: { sessionKey: `agent:${id}` },
      classList: { toggle: noop }, querySelector: selector => selector === '.del' ? remove : indicator,
      indicator, remove };
  });
  const h=vm.createContext({state,frames,calls,AbortController,Set,readSse,
    el:{sessionList:{querySelectorAll:()=>rows}},serviceUrl:x=>x,
    window:{DeepAgentConsole:{setConnectionStatus:noop}},
    sessionLookupKey:s=>`${s.agentId}:${s.id}`,updateHitlInputState:noop,
    stopHistoryObservation:noop,setStreaming:v=>{state.streaming=v;},
    applyAssistantPresentationActions:noop,assistantActionsFromSse:noop,
    clearComposerPlan:noop,failAutomaticContextCompaction:noop,addErrorTo:noop,
    upsertSession:(...args)=>calls.push(['session',...args]),agentNameById:id=>id,
    applySessionTitle:(s,title)=>calls.push(['title',s.id,title]),
    syncSessionsFromBackend:async()=>calls.push(['sync']),
    refreshSessionFilterOptions:async()=>{},
    loadSessionHistory:async s=>calls.push(['follow',s.id]),
    handleEvent:evt=>frames.push(evt),
    daFetch:async(_, {signal})=>({ok:true,body:new ReadableStream({start(controller){
      sockets.push({controller,signal});
      signal.addEventListener('abort',()=>controller.error(new DOMException('Aborted','AbortError')));
    }})}),
  });
  vm.runInContext(part('function detachChatView()', '\nfunction newChat(')
    + part('function updateSessionRunIndicators()', '\nasync function switchSession(')
    + part('async function runChatStream(', '\nfunction updateHitlInputState('),h);
  h.sockets=sockets;
  h.rows=rows;
  h.emit=(i,event,data={})=>sockets[i].controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  return h;
}
test('session list shows server-reported activity without clearing a newer run on late completion', () => {
  const h = streamHarness();
  h.state.sessions = [{ id: 'old', agentId: 'agent', activeRunId: 'current-run' }];
  h.updateSessionRunIndicators();
  assert.equal(h.rows[0].indicator.hidden, false);
  assert.equal(h.rows[1].indicator.hidden, true);
  h.clearListedSessionRun(h.state.sessions[0], 'previous-run');
  assert.equal(h.rows[0].indicator.hidden, false);
  h.clearListedSessionRun(h.state.sessions[0], 'current-run');
  assert.equal(h.rows[0].indicator.hidden, true);
});

test('switching leaves the producer connected; old text and final cannot alter the new view',async()=>{
  const h=streamHarness();const task=h.runChatStream({session_id:'old'},{});await tick();
  h.emit(0,'text',{delta:'visible'});await tick();
  h.detachChatView();h.state.sessionId='other';h.state.streaming=true;
  assert.equal(h.sockets[0].signal.aborted,false);
  h.emit(0,'text',{delta:'must not render'});h.emit(0,'final');await task;
  assert.equal(h.frames.length,1);assert.equal(h.state.streaming,true);
  assert.equal(h.state.sessionId,'other');assert.equal(h.state.backgroundStreams.size,0);
  assert.equal(h.sockets[0].signal.aborted,true);
});
test('returning before response headers follows the run when admitted and keeps the producer connected',async()=>{
  const h=streamHarness();const original=h.daFetch;let headers;
  h.daFetch=async(...args)=>{const response=await original(...args);await new Promise(r=>{headers=r;});return response;};
  const task=h.runChatStream({session_id:'old'},{});await tick();h.detachChatView();headers();await tick();
  h.emit(0,'session',{session_id:'old'});h.emit(0,'text',{delta:'background'});h.emit(0,'final');await task;
  assert.equal(h.frames.length,0);assert.equal(h.calls[0][0],'session');
  assert.deepEqual(h.calls[1],['follow','old']);
  assert.equal(h.state.backgroundStreams.size,0);
});
test('two producer connections remain isolated and terminating the old one does not unlock the new one',async()=>{
  const h=streamHarness();const first=h.runChatStream({session_id:'old'},{});await tick();h.detachChatView();
  assert.deepEqual(h.rows.map(row=>row.remove.hidden),[true,false]);
  h.state.sessionId='new';h.state.streaming=true;
  const second=h.runChatStream({session_id:'new'},{});await tick();
  assert.deepEqual(h.rows.map(row=>row.indicator.hidden),[false,false]);
  assert.deepEqual(h.rows.map(row=>row.remove.hidden),[true,true]);
  const latest=h.state.abortController;
  h.emit(0,'final');await first;
  assert.deepEqual(h.rows.map(row=>row.indicator.hidden),[true,false]);
  assert.deepEqual(h.rows.map(row=>row.remove.hidden),[false,true]);
  assert.equal(h.state.abortController,latest);assert.equal(h.state.streaming,true);assert.equal(latest.signal.aborted,false);
  h.detachChatView();h.emit(1,'final');await second;
  assert.deepEqual(h.rows.map(row=>row.indicator.hidden),[true,true]);
  assert.deepEqual(h.rows.map(row=>row.remove.hidden),[false,false]);
  assert.equal(h.state.backgroundStreams.size,0);
});

test('the console stream limit leaves a message draft and a pending interaction intact', async () => {
  const state = { sessionId: 'draft', agentId: 'a', pendingAttachments: ['attachment'],
    backgroundStreams: new Map(Array.from({length:5}, (_,i)=>[i,{id:String(i),agentId:'a'}])) };
  const el = { input: { value: 'unsent' } };
  const warnings = [];
  const h = vm.createContext({state,el, ensureAgentSelected:()=>true, selectedModelUnavailable:()=>false,
    showConsoleToast:message=>warnings.push(message)});
  vm.runInContext(part('const MAX_CONSOLE_CHAT_STREAMS', '\nasync function switchSession(')
    + part('async function onSend()', '\nasync function runChatStream('),h);
  await h.onSend();
  assert.equal(el.input.value,'unsent');
  assert.deepEqual(state.pendingAttachments,['attachment']);
  state.awaitingHitl=true;
  assert.equal(h.resumeDeferred([]),false);
  assert.equal(state.awaitingHitl,true);
  assert.equal(warnings.length,2);
  assert.match(warnings[0],/5 个会话正在生成/);
  state.backgroundStreams.delete(0);
  assert.equal(h.hasConsoleChatSlot(),true);
});

test('late context status cannot overwrite a switched session or a newly opened view of the same session',async()=>{
  for(const sameSession of [false,true]){
    let finish;const state={sessionId:'a',agentId:'agent',navigationRevision:0,contextStatus:{value:'initial'}};
    const h=vm.createContext({state,normalizeSession:x=>x,sessionApiUrl:()=>'',chatPayloadModelId:()=>'',updateComposerContextControls(){},
      daFetch:()=>new Promise(r=>{finish=r;}),
    });
    vm.runInContext(part('async function loadContextStatus(', '\nfunction compactTokenLabel('),h);
    const pending=h.loadContextStatus({id:'a',agentId:'agent'});
    state.sessionId=sameSession?'a':'b';state.navigationRevision++;state.contextStatus={value:'new'};
    finish({ok:true,json:async()=>({value:'old'})});await pending;
    assert.equal(state.contextStatus.value,'new');
  }
});
test('only the newest context request applies after switching models and back', async () => {
  const state = { sessionId: 's', agentId: 'a', llmModelId: 'first', contextStatus: null };
  const requests = [];
  const h = vm.createContext({
    state, normalizeSession: session => session, sessionApiUrl: (_session, _suffix, { model_id }) => model_id,
    chatPayloadModelId: () => state.llmModelId, updateComposerContextControls() {},
    daFetch: model => new Promise(resolve => requests.push({ model, resolve })),
  });
  vm.runInContext(part('async function loadContextStatus(', '\nfunction compactTokenLabel('), h);
  const session = { id: 's', agentId: 'a' };
  const loads = ['first', 'second', 'first'].map(model => {
    state.llmModelId = model;
    return h.loadContextStatus(session);
  });
  for (const index of [2, 1, 0]) {
    requests[index].resolve({ ok: true, json: async () => ({ model: requests[index].model, request: index }) });
    await loads[index];
  }
  assert.deepEqual(state.contextStatus, { model: 'first', request: 2 });
});

test('late UI tool metadata cannot replace the selected Agent tool definitions',async()=>{
  let finish;const state={agentId:'a',navigationRevision:0};const uiToolsByName=new Map([['new-tool',{}]]);
  const h=vm.createContext({state,uiToolsByName,URLSearchParams,serviceUrl:x=>x,console,
    daFetch:()=>new Promise(r=>{finish=r;}),
  });
  vm.runInContext(part('async function loadUiTools(', '\nconst state ='),h);
  const pending=h.loadUiTools('a');state.agentId='b';state.navigationRevision++;
  finish({ok:true,json:async()=>({items:[{name:'old-tool'}]})});await pending;
  assert.deepEqual([...uiToolsByName.keys()],['new-tool']);
});
