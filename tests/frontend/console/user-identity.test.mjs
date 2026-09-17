import test from 'node:test';
import assert from 'node:assert/strict';
import { renderUserIdentity, initUserIdentityControls } from '../../../console/user-identity.js';

test('username and email are escaped; nickname, avatars and expanded ID are absent', () => {
 const html = renderUserIdentity({username:'<owner>',nickname:'Nick',email:'a"@example.test',avatar_url:'https://example.test/p.png'}, {userId:'full-original-id'});
 assert.match(html, /&lt;owner&gt;/); assert.match(html, /a&quot;@example.test/);
 assert.doesNotMatch(html, /<owner>|Nick|<img|data-user-reveal|da-user-raw|aria-expanded/);
 assert.equal((html.match(/data-user-copy="full-original-id"/g)||[]).length, 1);
});
test('list and search have no actions; compact identity uses username only', () => {
 const profile={username:'name',nickname:'昵称',email:'name@example.test'};
 assert.doesNotMatch(renderUserIdentity(profile,{userId:'4',copyable:false}), /button|data-user-copy|data-lucide/);
 const simple=renderUserIdentity(profile,{userId:'4',compact:true});
 assert.match(simple, /name/); assert.doesNotMatch(simple, /昵称|button|example.test/);
 assert.match(renderUserIdentity(profile,{userId:'4',userIcon:true}), /circle-user-round/);
});
test('missing and unavailable profiles retain UUID, numeric IDs, and different status labels', () => {
 assert.match(renderUserIdentity({}, {userId:'4'}), />未查到<\/span>/);
 assert.match(renderUserIdentity({resolution:'unavailable'}, {userId:'4'}), />查询失败<\/span>/);
 assert.match(renderUserIdentity({}, {userId:'4'}), /data-user-copy="4"/);
 const id='189faa0f-9f48-4731-a475-09c3f7580b98';
 assert.match(renderUserIdentity({}, {userId:id,copyable:false}), new RegExp(id));
});
test('copy stops row navigation and shows success only after the full ID is copied', async () => {
 const listeners=new Map(), classes=new Set();
 const dispose=initUserIdentityControls({addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n)});
 let stopped=0, copied='';
 const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async value=>{copied=value;}}}});
 try {
  const copy={dataset:{userCopy:'4'},classList:{add:n=>classes.add(n),remove:n=>classes.delete(n)},setAttribute(){}};
  await listeners.get('click')({target:{closest:()=>copy},preventDefault(){},stopPropagation(){stopped++;}});
  assert.equal(copied,'4'); assert.equal(stopped,1); assert.equal(classes.has('is-copied'),true);
  navigator.clipboard.writeText=async()=>{throw Error('denied');};
  await listeners.get('click')({target:{closest:()=>copy},preventDefault(){},stopPropagation(){}});
  assert.equal(classes.has('is-copied'),false);
 } finally {dispose(); if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;}
 assert.equal(listeners.size,0);
});
test('pending copies cannot race and disposal ignores their late success', async () => {
 const listeners=new Map(), classes=new Set();
 const dispose=initUserIdentityControls({addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n)});
 const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 let finish, calls=0;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:()=>{calls++;return new Promise(resolve=>{finish=resolve;});}}}});
 try {
  const button={dataset:{userCopy:'original-id'},classList:{add:n=>classes.add(n),remove:n=>classes.delete(n)},setAttribute(){}};
  const event={target:{closest:()=>button},preventDefault(){},stopPropagation(){}};
  const click=listeners.get('click');
  const first=click(event);
  assert.equal(button.disabled,true);
  await click(event);
  assert.equal(calls,1);
  dispose(); finish(); await first;
  assert.equal(classes.has('is-copied'),false);
  assert.equal(button.disabled,false);
 } finally {dispose(); if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;}
});
