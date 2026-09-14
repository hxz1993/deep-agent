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
