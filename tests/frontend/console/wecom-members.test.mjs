import test from 'node:test';
import assert from 'node:assert/strict';
import { createMemberQuery } from '../../../console/configuration/wecom-members.js';
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const result = (items = [], total = items.length) => ({items,total});
const settle = () => new Promise(setImmediate);
function harness(request) {
  const calls=[], changes=[], timers=new Map(); let timer=0;
  const query=createMemberQuery({applicationId:'app / one', request:(url,options)=>{calls.push({url,...options});return request(url,options)}, onChange:s=>changes.push({...s}), schedule:fn=>{timers.set(++timer,fn);return timer},unschedule:id=>timers.delete(id)});
  return {query,calls,changes,timers};
}
test('lazy query starts no reads until load, encodes all server-side filters and page offset',async()=>{
  const h=harness(async()=>result([],30)); assert.equal(h.calls.length,0);
  await h.query.load(); assert.match(h.calls[0].url,/app%20%2F%20one\/cache\/members/);
  await h.query.update({q:'a&%_',status:'stale',synced:'week',limit:20});
  await h.query.update({offset:20}); const p=new URL('http://local'+h.calls.at(-1).url).searchParams;
  assert.equal(p.get('q'),'a&%_');assert.equal(p.get('status'),'stale');assert.equal(p.get('synced'),'week');assert.equal(p.get('offset'),'20');
  h.query.dispose();
});
test('typing aborts old reads immediately, debounces and ignores late responses',async()=>{
  const old=deferred();let n=0;const h=harness(()=>++n===1?old.promise:Promise.resolve(result([{id:'new'}])));
  const first=h.query.load();h.query.update({q:'new'},{debounce:true});assert.equal(h.calls[0].signal.aborted,true);
  old.resolve(result([{id:'old'}]));await first;assert.deepEqual(h.query.state.items,[]);
  [...h.timers.values()][0]();await settle();assert.equal(h.query.state.items[0].id,'new');h.query.dispose();
});
test('leaving or switching app aborts and discards late results and scheduled search',async()=>{
  const pending=deferred();const h=harness(()=>pending.promise);const load=h.query.load();h.query.dispose();pending.resolve(result([{id:'late'}]));await load;
  assert.equal(h.calls[0].signal.aborted,true);assert.deepEqual(h.query.state.items,[]);const count=h.changes.length;await h.query.load();assert.equal(h.changes.length,count);
  const h2=harness(async()=>result());h2.query.update({q:'cancel'},{debounce:true});h2.query.dispose();assert.equal(h2.timers.size,0);assert.equal(h2.calls.length,0);
});
test('failed requests show failure and can retry without stale rows',async()=>{
  let fail=false;const h=harness(async()=>{if(fail)throw Error('权限不足');return result([{id:'one'}])});
  await h.query.load();fail=true;await h.query.load();assert.equal(h.query.state.error,'权限不足');assert.deepEqual(h.query.state.items,[]);
  fail=false;await h.query.load();assert.equal(h.query.state.error,'');assert.equal(h.query.state.items.length,1);h.query.dispose();
});
test('deleted rows on the last page clamp to final remaining page',async()=>{
  const h=harness(async url=>result([{id:'one'}],11));await h.query.update({offset:20});
  assert.equal(h.calls.length,2);assert.equal(h.query.state.offset,10);assert.equal(h.query.state.loading,false);h.query.dispose();
});
test('malformed API result remains an error instead of a misleading empty state',async()=>{
  const h=harness(async()=>({items:null,total:3}));await h.query.load();assert.match(h.query.state.error,/格式不正确/);h.query.dispose();
});
