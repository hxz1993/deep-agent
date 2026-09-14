import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { channelEntryFilters, diagnosticHash, parseDiagnosticRoute, sessionRange } from "../../../console/diagnostics-navigation.js";
import { createSessionReader, sessionQueryError } from "../../../console/diagnostics-session-query.js";
import { escapeHtml as e, icon, selectHtml } from "../../../console/diagnostics-ui.js";
const source = await readFile(new URL("../../../console/diagnostics-channels.js", import.meta.url), "utf8");
const settle = async () => { for(let i=0;i<4;i++) await new Promise(setImmediate); };
const later = () => { let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve}; };
const peer = (id="member",kind="user") => ({peer_id:id,kind,name:null,session_count:2,question_count:3,latest_session:{session_id:`${id}-session`,question_count:2,latest_question:"最新问题"}});
const history = id => ({items:[{session_id:`${id}-session`,question_count:2,is_latest:true,latest_question:"最新问题"}],total:1});
function harness(request) {
 const pickerCallbacks={}, listeners={}, calls=[], navigations=[], copies=[], inspector={innerHTML:"",querySelector:()=>null};
 const root={innerHTML:"",addEventListener:(n,f)=>listeners[n]=f,querySelector:()=>inspector,replaceChildren(){this.innerHTML="";}};
 const context=vm.createContext({URLSearchParams,Date,Intl,AbortController,e,icon,icons(){},selectHtml,
  peerPickerHtml(){return "";},bindPeerPicker(root, options){pickerCallbacks[options.kind]=options;return()=>{};},bindChannelResizer(){return()=>{};},bindDiagnosticSelects(){return{dispose(){}};},diagnosticHash,sessionRange,channelEntryFilters,createSessionReader,sessionQueryError,
  shortQuestionTime:v=>v||"—",navigator:{clipboard:{writeText:async id=>copies.push(id)}},
  diagnosticRequest:(path,signal)=>{calls.push({path,signal});return request(path,signal);}});
 vm.runInContext(source.replace(/^import .*;\n/gm,"").replace(/^export /gm,""),context);
 const controller=context.initDiagnosticChannels({root,navigate:h=>navigations.push(h),replace(){}});
 return {pickerCallbacks,root,inspector,controller,context,calls,copies,navigations,activate:()=>controller.activate({filters:{period:"168"}}),click:dataset=>listeners.click({target:{closest:()=>({dataset})}})};
}
function response(path) {
 if(path.endsWith("/accounts")) return {items:[{id:"one",name:"渠道一",enabled:true},{id:"two",name:"渠道二",enabled:true}]};
 if(path.includes("/overview?")) return {summary:{active_users:1,question_count:3,session_count:2},active_users:[]};
 if(path.includes("/peers?")) return {items:[peer()],total:1};
 return history("member");
}
test("channel entry carries only the relative period; custom time has a seven-day fallback",()=>{
 const f={period:"720",agent_id:"x",source:"channel",query:"private",user_id:"y",start:"2026-09-01",offset:50};
 assert.equal(diagnosticHash("channels",channelEntryFilters(f)),"#/diagnostics/sessions/channels?period=720");
 assert.deepEqual(channelEntryFilters({...f,period:"custom"}),{period:"168"});
 const parsed=parseDiagnosticRoute("#/diagnostics/sessions/channels?period=24&account_id=a&agent_id=x&token=secret");
 assert.equal(parsed.view,"channels");assert.equal(parsed.id,null);assert.deepEqual(parsed.filters,{period:"24",account_id:"a"});
});
test("switching accounts aborts old page/history requests and ignores late responses",async()=>{
 const pending=later();const h=harness(path=>path.includes("/one/peers?")?pending.promise:response(path));
 h.activate();await settle();const old=h.calls.find(c=>c.path.includes("/one/peers?"));
 await h.click({channelAccount:"two"});await settle();assert.equal(old.signal.aborted,true);
 pending.resolve({items:[peer("obsolete")],total:1});await settle();assert.doesNotMatch(h.root.innerHTML,/obsolete/);assert.equal(h.controller.snapshot().filters.account_id,"two");
 h.controller.deactivate();assert.ok(h.calls.at(-1).signal.aborted);
});
test("peers select the first result, groups get their own fallback identity, and details reuse diagnostic routing",async()=>{
 const h=harness(path=>path.includes("kind=group")?path.includes("/peers?")?{items:[peer("chat-id","group")],total:1}:history("chat-id"):response(path));
 h.activate();await settle();assert.equal(h.controller.snapshot().selected.peer_id,"member");
 await h.click({channelKind:"group"});await settle();assert.match(h.root.innerHTML,/群聊/);assert.match(h.root.innerHTML,/channel-avatar group/);
 await h.click({channelOpen:"full-session-id"});assert.equal(h.navigations.at(-1),"#/diagnostics/sessions/full-session-id?view=conversation");
 await h.click({channelCopy:"full-session-id"});assert.deepEqual(h.copies,["full-session-id"]);
 h.controller.clear();assert.equal(h.root.innerHTML,"");
});
test("switching peers rejects late history responses from the previous peer",async()=>{
 const pending=later();let historyCalls=0;
 const h=harness(path=>{
  if(path.includes("/peers?"))return{items:[peer("a"),peer("b")],total:2};
  if(path.includes("/sessions?")){historyCalls++;if(historyCalls===1)return pending.promise;return history("b");}
  return response(path);
 });h.activate();await settle();await h.click({channelPeer:"b"});await settle();pending.resolve(history("a"));await settle();
 assert.equal(h.controller.snapshot().sessions.items[0].session_id,"b-session");assert.doesNotMatch(h.inspector.innerHTML,/a-session/);
 h.controller.deactivate();
});
test("session markup escapes question content and ID; latest uses compact tag without status",()=>{
 const h=harness(response);const html=h.context.channelSessionHtml({session_id:'<id"',is_latest:true,latest_question:'<img onerror="x">',question_count:4});
 assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|已完成|最近会话/);assert.match(html,/channel-latest-label">最新/);assert.match(html,/4 条提问/);
});

test("user selection and search survive tab switches; group selection stays independent", async () => {
 const h=harness(response); h.activate(); await settle();
 h.pickerCallbacks.user.onQuery("Alice");
 h.pickerCallbacks.user.onSelect({kind:"user",peer_id:"member",name:"Alice"}); await settle();
 await h.click({channelKind:"group"}); await settle();
 assert.equal(h.controller.snapshot().filters.channel_user_id,"member");
 assert.equal(h.pickerCallbacks.user.initialQuery,"Alice");
 assert.ok(h.calls.some(c=>c.path.includes("kind=group") && c.path.includes("sender_id=member")));
 h.pickerCallbacks.group.onSelect({kind:"group",peer_id:"group-1"}); await settle();
 await h.click({channelKind:"user"}); await settle();
 assert.equal(h.controller.snapshot().filters.channel_user_id,"member");
 assert.equal(h.controller.snapshot().filters.channel_group_id,"group-1");
 const privateCall=h.calls.filter(c=>c.path.includes("/peers?")).at(-1).path;
 assert.ok(privateCall.includes("peer_id=member")); assert.ok(!privateCall.includes("sender_id"));
 await h.click({channelKind:"group"}); await settle();
 const groupCall=h.calls.filter(c=>c.path.includes("/peers?")).at(-1).path;
 assert.ok(groupCall.includes("peer_id=group-1") && groupCall.includes("sender_id=member"));
 h.controller.deactivate();
});

test("ranking selection filters by sender while preserving the chosen group", async () => {
 const h=harness(path=>path.includes("/overview?") ? {summary:{},active_users:[{user_id:"ranked-user",name:"Alice",question_count:4}]} : response(path));
 h.activate(); await settle();
 await h.click({channelKind:"group"}); await settle();
 h.pickerCallbacks.group.onSelect({kind:"group",peer_id:"chosen-group"}); await settle();
 await h.click({channelUser:"ranked-user"}); await settle();
 const snapshot=h.controller.snapshot();
 assert.equal(snapshot.filters.kind,"group");
 assert.equal(snapshot.filters.channel_group_id,"chosen-group");
 assert.equal(snapshot.filters.channel_user_id,"ranked-user");
 assert.equal(snapshot.userChoice.name,"Alice");
 const query=new URL(h.calls.filter(c=>c.path.includes("/peers?")).at(-1).path,"http://localhost").searchParams;
 assert.equal(query.get("peer_id"),"chosen-group"); assert.equal(query.get("sender_id"),"ranked-user");
 h.controller.deactivate();
});

test("clearing diagnostics removes cached identities and searches before another login", async () => {
 const h=harness(response); h.activate(); await settle();
 h.pickerCallbacks.user.onQuery("private-account-search");
 h.pickerCallbacks.user.onSelect({kind:"user",peer_id:"member",name:"Private name"}); await settle();
 h.controller.clear();
 const cleared=h.controller.snapshot();
 assert.equal(cleared.userChoice,null); assert.equal(cleared.groupChoice,null);
 assert.equal(cleared.pickerQueries.user,""); assert.equal(cleared.pickerQueries.group,"");
 assert.equal(Object.keys(cleared.filters).length,0);
 h.activate(); await settle();
 assert.equal(h.pickerCallbacks.user.initialQuery,"");
 h.controller.deactivate();
});
