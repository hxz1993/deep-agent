import test from 'node:test';
import assert from 'node:assert/strict';
import {uniqueChannelPeers, peerPickerHtml} from '../../../console/channel-peer-picker.js';

test('identity dedupe prefers names without merging namesakes or case-sensitive IDs', () => {
  const result = uniqueChannelPeers([
    {kind:'user',peer_id:'a'}, {kind:'user',peer_id:'a',name:'同名'},
    {kind:'user',peer_id:'b',name:'同名'}, {kind:'user',peer_id:'A'},
  ]);
  assert.equal(result.length,3);
  assert.equal(result[0].name,'同名');
  assert.match(peerPickerHtml('user', {peer_id:'raw-id'}), /raw-id/);
  assert.doesNotMatch(peerPickerHtml('user', {peer_id:'id',name:'<img>'}), /<img>/);
});

test('ArrowUp enters at the last result and disposal aborts delayed searches', async () => {
  const {bindPeerPicker} = await import('../../../console/channel-peer-picker.js');
  const listeners = {}, focused = [], buttons = [0,1,2].map(id => ({focus:()=>focused.push(id)}));
  const input = {value:'', focus(){}, setAttribute(){}, addEventListener(n,fn){listeners[n]=fn;},removeEventListener(){}};
  const list = {innerHTML:'',querySelectorAll:()=>buttons,addEventListener(){},removeEventListener(){}};
  const host = {open:true,querySelector:s=>s==='input'?input:list,addEventListener(n,fn){listeners[n]=fn;},removeEventListener(){}};
  const root = {querySelector:()=>host,ownerDocument:{addEventListener(){},removeEventListener(){}}};
  let resolvePage, signal;
  const originalWindow=globalThis.window; globalThis.window={};
  try {
    const dispose=bindPeerPicker(root,{kind:'user',fetchPage:(_query,_offset,s)=>{signal=s;return new Promise(resolve=>{resolvePage=resolve;});},onSelect(){}});
    listeners.keydown({key:'ArrowUp',target:input,preventDefault(){}});
    assert.deepEqual(focused,[2]);
    listeners.toggle(); const loading=list.innerHTML;
    dispose(); assert.equal(signal.aborted,true);
    resolvePage({items:[{kind:'user',peer_id:'stale'}],total:1});
    await new Promise(setImmediate);
    assert.equal(list.innerHTML,loading);
  } finally { globalThis.window=originalWindow; }
});


test('incomplete name search warns with retry instead of claiming no result; disabled search is ID-only', async () => {
  const {bindPeerPicker} = await import('../../../console/channel-peer-picker.js');
  const listeners = {}, attributes = {};
  const input = {value:'', focus(){}, setAttribute(k,v){attributes[k]=v;}, addEventListener(n,fn){listeners[n]=fn;},removeEventListener(){}};
  const list = {innerHTML:'',addEventListener(n,fn){listeners[`list-${n}`]=fn;},removeEventListener(){}};
  const host = {open:true,querySelector:s=>s==='input'?input:list,addEventListener(n,fn){listeners[n]=fn;},removeEventListener(){}};
  const root = {querySelector:()=>host,ownerDocument:{addEventListener(){},removeEventListener(){}}};
  const originalWindow=globalThis.window; globalThis.window={};
  let complete=false, calls=0;
  try {
    const dispose=bindPeerPicker(root,{kind:'user',initialQuery:'张三',fetchPage:async()=>{calls++;return {items:[],total:0,profiles_complete:complete,user_details_enabled:!complete};},onSelect(){}});
    listeners.toggle(); await new Promise(setImmediate);
    assert.match(list.innerHTML,/姓名搜索结果可能不完整/);
    assert.match(list.innerHTML,/data-peer-retry/);
    assert.doesNotMatch(list.innerHTML,/没有匹配结果/);
    complete=true;
    listeners['list-click']({target:{closest:s=>s==='[data-peer-retry]'?{}:null}});
    await new Promise(setImmediate); assert.equal(calls,2);
    assert.match(list.innerHTML,/仅支持 ID 搜索/);
    assert.equal(attributes.placeholder,'搜索用户 ID');
    dispose();
  } finally { globalThis.window=originalWindow; }
  assert.match(peerPickerHtml('user', null, false),/placeholder="搜索用户 ID"/);
});
