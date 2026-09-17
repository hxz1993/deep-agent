import assert from 'node:assert/strict';
import test from 'node:test';
import { savedSessionCounts, savedRunFacts, savedRunState, savedRunPresentation, renderContextReferenceContent } from '../../../console/session-presentation.js';
import { setSessionLoading, prepareSessionRender } from '../../../console/session-loading.js';

const execution = (sequence, event_type, run_id, status) => ({sequence, event_type, run_id, status});

test('latest saved execution error overrides an earlier assistant completion without mutating event order', () => {
  const events = [execution(3, 'execution/error', 'run'), execution(1, 'user/message', 'run'), execution(2, 'assistant/message', 'run', 'completed')];
  assert.deepEqual(savedRunState(events), {run_id:'run', status:'error'});
  assert.deepEqual(events.map(event => event.sequence), [3,1,2]);
  assert.deepEqual(savedRunState([...events, execution(4, 'tool/results', 'run', 'completed')]), {run_id:'run', status:'error'});
  assert.deepEqual(savedRunState([...events, execution(5, 'assistant/message', 'run', 'completed')]), {run_id:'run', status:'completed'});
});

test('new run without an assistant does not borrow previous run completion or waiting status', () => {
  for (const initial of ['completed', 'pending']) {
    for (const kind of ['user/message', 'tool/results', 'model/request']) {
      const events = [execution(1, 'assistant/message', 'old', initial), execution(2, kind, 'new', 'completed')];
      assert.deepEqual(savedRunState(events), {run_id:'new', status:'unknown'});
    }
  }
  assert.deepEqual(savedRunState([execution(1, 'assistant/message', 'old', 'completed'), execution(2, 'execution/error', 'new')]), {run_id:'new', status:'error'});
});

test('saved state respects null run scope, actual assistant terminals and non-execution events', () => {
  assert.deepEqual(savedRunState([]), {run_id:null, status:'not_started'});
  assert.deepEqual(savedRunState([execution(1, 'session/title', 'run'), execution(2, 'compaction', 'run')]), {run_id:null, status:'not_started'});
  const statuses = {pending:'waiting', completed:'completed', interrupted:'stopped', cancelled:'stopped', failed:'error', running:'unknown'};
  for (const [saved, expected] of Object.entries(statuses)) {
    assert.deepEqual(savedRunState([execution(1, 'assistant/message', 'run', saved), execution(2, 'session/title', 'other')]), {run_id:'run', status:expected});
  }
  assert.deepEqual(savedRunState([execution(1, 'assistant/message', 'old', 'completed'), execution(2, 'user/message', null)]), {run_id:null, status:'unknown'});
  assert.deepEqual(savedRunState([execution(1, 'assistant/message', 'old', 'completed'), execution(2, 'execution/error', null), execution(3, 'tool/results', null)]), {run_id:null, status:'error'});
});

test('saved message totals merge parts including ordinal zero and scope ordinals to each run', () => {
  const event = (id, type, run, ordinal, turn='question') => ({event_id:id,event_type:type,run_id:run,turn_id:turn,payload:ordinal===undefined ? {} : {source:{message_ordinal:ordinal}}});
  const events = [event('u','user/message','a',0),event('a0','assistant/message','a',1),event('a1','assistant/message','a',1),event('m','model/request','a',2,'not-a-question'),event('u2','user/message','b',0,'question2'),event('t','tool/results','b',undefined),event('p','run/progress','b',3)];
  assert.deepEqual(savedSessionCounts(events),{messageCount:5,turnCount:2});
});

test('saved run presentation sums complete responses for that run, ignores zero cache defaults and other runs', () => {
  const response = (run,ordinal,part,tokens) => ({event_id:`${run}-${ordinal}-${part}`,run_id:run,event_type:'assistant/message',sequence:ordinal*2+part,payload:{source:{message_ordinal:ordinal,part_index:part,part_key:String(part)},message:{kind:'response',model_name:`model-${run}`,usage:{input_tokens:tokens,output_tokens:5,cache_read_tokens:0},parts:[{part_kind:'text',content:'answer'}]}}});
  const events = [response('r',0,0,10),response('r',0,1,10),response('r',1,0,20),response('other',2,0,999)];
  const facts = savedRunFacts(events,'r');
  assert.deepEqual(facts.usage,{input_tokens:30,output_tokens:10,total_tokens:40});
  const presentation = savedRunPresentation({run_id:'r',model_name:facts.model_name,runs:[{run_id:'r',...facts}]});
  assert.equal(presentation.model.name,'model-r');
  assert.equal(presentation.runModel.name,'model-r');
  assert.deepEqual(presentation.usage,facts.usage);
});

test('both hosts use escaped, redacted context facts and one empty state', () => {
  const html = renderContextReferenceContent({label:'<img onerror=x>',key:'cube',type:'selection',schema_version:0,payload:{token:'do-not-expose'}});
  assert.match(html,/&lt;img/);
  assert.match(html,/名称/); assert.match(html,/版本/); assert.match(html,/>0</);
  assert.doesNotMatch(html,/do-not-expose|<img/);
  assert.match(renderContextReferenceContent(null),/尚未选择上下文引用/);
});

test('shared loading locks only host-selected content and releases it on completion', async () => {
  const attributes = new Map(); let hidden=true;
  const label={textContent:''};
  const overlay={classList:{toggle:(name,value)=>{hidden=value;}},querySelector:()=>label};
  const root={querySelector:()=>overlay,toggleAttribute:(name,value)=>value?attributes.set(name,''):attributes.delete(name),setAttribute:(name,value)=>attributes.set(name,value)};
  const chat={},inspector={},sidebar={};
  setSessionLoading(root,[chat,inspector]);
  assert.equal(hidden,false); assert.equal(chat.inert,true); assert.equal(inspector.inert,true); assert.equal(sidebar.inert,undefined);
  assert.equal(attributes.get('aria-busy'),'true'); assert.equal(label.textContent,'正在加载会话…');
  await prepareSessionRender();
  setSessionLoading(root,[chat,inspector],false);
  assert.equal(hidden,true); assert.equal(chat.inert,false); assert.equal(inspector.inert,false); assert.equal(attributes.has('aria-busy'),false);
});
