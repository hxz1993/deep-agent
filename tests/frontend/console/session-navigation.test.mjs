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
    loadAgents: async () => {}, syncSessionsFromBackend: async () => {},
    sessionApiUrl: s => s.id, sessionLookupKey: s => `${s.agentId}:${s.id}`,
    daFetch: async id => { calls.push(['get',id]); return {status,ok:status===200}; },
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
  const h=vm.createContext({state,frames,calls,AbortController,Set,readSse,
    el:{},serviceUrl:x=>x,setConn:noop,
    stopHistoryObservation:noop,setStreaming:v=>{state.streaming=v;},
    applyAssistantPresentationActions:noop,assistantActionsFromSse:noop,
    clearComposerPlan:noop,failAutomaticContextCompaction:noop,addErrorTo:noop,
    upsertSession:(...args)=>calls.push(['session',...args]),agentNameById:id=>id,
    applySessionTitle:(s,title)=>calls.push(['title',s.id,title]),
    syncSessionsFromBackend:async()=>calls.push(['sync']),
    handleEvent:evt=>frames.push(evt),
    daFetch:async(_, {signal})=>({ok:true,body:new ReadableStream({start(controller){
      sockets.push({controller,signal});
      signal.addEventListener('abort',()=>controller.error(new DOMException('Aborted','AbortError')));
    }})}),
  });
  vm.runInContext(part('function detachChatView()', '\nfunction newChat(')+part('async function runChatStream(', '\nfunction updateHitlInputState('),h);
  h.sockets=sockets;
  h.emit=(i,event,data={})=>sockets[i].controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  return h;
}
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
test('switching before response headers still drains the admitted run until its terminal frame',async()=>{
  const h=streamHarness();const original=h.daFetch;let headers;
  h.daFetch=async(...args)=>{const response=await original(...args);await new Promise(r=>{headers=r;});return response;};
  const task=h.runChatStream({session_id:'old'},{});await tick();h.detachChatView();headers();await tick();
  h.emit(0,'session',{session_id:'old'});h.emit(0,'text',{delta:'background'});h.emit(0,'final');await task;
  assert.equal(h.frames.length,0);assert.equal(h.calls[0][0],'session');
  assert.equal(h.state.backgroundStreams.size,0);
});
test('two producer connections remain isolated and terminating the old one does not unlock the new one',async()=>{
  const h=streamHarness();const first=h.runChatStream({session_id:'old'},{});await tick();h.detachChatView();
  h.state.sessionId='new';h.state.streaming=true;
  const second=h.runChatStream({session_id:'new'},{});await tick();
  const latest=h.state.abortController;
  h.emit(0,'final');await first;
  assert.equal(h.state.abortController,latest);assert.equal(h.state.streaming,true);assert.equal(latest.signal.aborted,false);
  h.detachChatView();h.emit(1,'final');await second;
  assert.equal(h.state.backgroundStreams.size,0);
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
