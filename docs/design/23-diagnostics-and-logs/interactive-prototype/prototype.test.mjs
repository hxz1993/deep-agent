import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sessions,logs,filterSessions,filterLogs,aggregate,questionRows,escapeHTML,NOW} from './data.js';

test('unique fictional session and log identifiers',()=>{
  assert.equal(new Set(sessions.map(s=>s.id)).size,sessions.length);
  assert.equal(new Set(logs.map(l=>l.id)).size,logs.length);
  assert.ok(sessions.every(s=>/^[a-f0-9]{32}$/.test(s.id)));
});
test('overview counts and question collection derive from the same filtered sessions',()=>{
  const items=filterSessions({agent:'企微助手',days:'7'}),n=aggregate(items);
  assert.equal(n.sessions,items.length);
  assert.equal(n.questions,questionRows(items).length);
  assert.equal(n.users,new Set(items.map(s=>s.user)).size);
  assert.equal(n.failed,items.filter(s=>s.status==='failed').length);
});
test('title search is fuzzy and complete session ID is exact',()=>{
  const s=sessions[0];assert.ok(filterSessions({q:'数据流'}).some(v=>v.id===s.id));
  assert.deepEqual(filterSessions({q:` ${s.id.toUpperCase()} `}),[s]);
  assert.equal(filterSessions({q:s.id.slice(0,8)}).length,0);
  assert.equal(filterSessions({q:'<script>'}).length,0);
});
test('all session filters combine without widening scope',()=>{
  const s=sessions[1];assert.deepEqual(filterSessions({q:s.id,agent:s.agent,user:s.user,channel:s.channel,status:s.status,days:'1'}),[s]);
  assert.equal(filterSessions({q:s.id,user:'不存在'}).length,0);
  assert.ok(filterSessions({days:'1'}).every(s=>Date.parse(s.updated)>=NOW-86400000));
});
test('attention filter is not equated with failure',()=>{
  const waiting=filterSessions({wait:true});assert.ok(waiting.some(s=>s.status==='running'));
  assert.ok(filterSessions({status:'waiting'}).every(s=>s.status==='waiting'));
});
test('questions preserve follow-ups and session linkage in reverse time order',()=>{
  const rows=questionRows(sessions);assert.equal(rows.filter(q=>q.session.id===sessions[0].id).length,2);
  assert.ok(rows.every((q,i)=>i===0||rows[i-1].at>=q.at));
});
test('every log links to its own session and Run',()=>{
  assert.ok(logs.every(l=>sessions.some(s=>s.id===l.sessionId&&s.runId===l.runId)));
  const s=sessions[0];assert.ok(filterLogs({sessionId:s.id}).length);
  assert.ok(filterLogs({sessionId:s.id}).every(l=>l.sessionId===s.id));
  assert.equal(filterLogs({sessionId:s.id,runId:sessions[1].runId}).length,0);
});
test('log keyword is plain text, combined with source level and time',()=>{
  const results=filterLogs({q:'provider_timeout',source:'API',level:'ERROR',hours:'24'});
  assert.ok(results.length);assert.ok(results.every(l=>l.level==='ERROR'&&l.source==='API'));
  assert.equal(filterLogs({q:'.*'}).length,0);
  assert.equal(filterLogs({q:'<img onerror='}).length,0);
});
test('failed sessions never emit completed or delivered fixture logs',()=>{
  for(const s of sessions.filter(s=>s.status==='failed')){
    const rows=filterLogs({sessionId:s.id});
    assert.ok(!rows.some(l=>l.event==='agent.completed'||l.event==='channel.delivered'));
  }
});
test('HTML escaping covers text and attribute delimiters',()=>{
  assert.equal(escapeHTML('<a x="a&b">\'x\'</a>'),'&lt;a x=&quot;a&amp;b&quot;&gt;&#39;x&#39;&lt;/a&gt;');
});
test('static prototype disallows business connections and uses only local production assets',()=>{
  const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');
  assert.match(html,/connect-src 'none'/);assert.match(html,/data-theme="light"/);
  assert.doesNotMatch(html,/(?:src|href)="https?:/);
  assert.doesNotMatch(html,/src=".*(?:chat|workspace|main|api)\.js/);
});
test('log row contains only Session ID on right, without status dots or Run column',()=>{
  const app=readFileSync(new URL('./app.js',import.meta.url),'utf8');
  const row=app.slice(app.indexOf('<div class="log-stream"'),app.indexOf('<footer class="log-footer"'));
  assert.match(row,/log-session/);assert.doesNotMatch(row,/runId|status-dot|class="dot"/);
});
