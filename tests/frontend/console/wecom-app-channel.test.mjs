import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../../console/configuration/channels.js', import.meta.url), 'utf8');
function harness() {
  const elements = new Map();
  const ready = [];
  const listeners = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { addEventListener(type, listener){ listeners.set(`${id}:${type}`, listener); }, value:'', dataset:{}, disabled:false, hidden:false, getAttribute:()=> 'false', classList:{add(){},remove(){}}, close(){ this.closed = true; }, showModal(){ this.opened = true; } });
    return elements.get(id);
  };
  const requests = [];
  const context = vm.createContext({window:{location:{origin:"https://alpha.deepfos.com"}}, navigator:{clipboard:{writeText:async()=>{}}}, document:{addEventListener(type, fn){ if(type==='DOMContentLoaded') ready.push(fn); }, getElementById:element, querySelectorAll:()=>[]}, escapeHtml:String,
    apiFetch:async (path, options)=> { requests.push({path,payload:JSON.parse(options.body)}); return {id:'saved-id'}; },
    AdminFormFeedback:{show(){throw Error('unexpected validation failure')}}, showToast(){},
    refreshAfterSuccessfulMutation:async (_, cb)=>cb(),
  });
  vm.runInContext(source,context);
  vm.runInContext('channelAdminState.channelType="wecom_app"; channelsAdmin.refresh=async()=>{}; channelsAdmin.openEdit=id=>{window.opened=id};',context);
  return {context,element,requests,ready,listeners};
}
test('Webhook editor preserves masked credentials and builds current-origin URL only after saving',()=>{
  const h=harness();
  vm.runInContext('prepareChannelWebhookEditor()',h.context);
  assert.equal(h.element('channelWebhookToken').required,true);
  assert.equal(h.element('channelFullId').hidden,true);
  assert.equal(h.element('copyChannelWebhookUrl').disabled,true);
  vm.runInContext('prepareChannelWebhookEditor({id:"abc",webhook_token_set:true,webhook_token_masked:"***",webhook_aes_key_set:true,webhook_url:"https://hooks.example/abc"})',h.context);
  assert.equal(h.element('channelWebhookToken').required,false);
  assert.equal(h.element('channelWebhookToken').value,'***');
  assert.equal(h.element('channelFullIdValue').textContent,'abc');
  assert.equal(h.element('channelWebhookUrl').value,'https://alpha.deepfos.com/webhooks/agent/channel/abc');
});
test('Webhook save sends independent credentials without Bot fields, closes editor and opens creation result',async()=>{
  const h=harness();
  vm.runInContext('channelAdminState.applications=[{id:"app"}];',h.context);
  for (const [id,value] of Object.entries({channelWebhookApplicationId:'app',channelName:'应用',channelAgentId:'agent',channelSessionUserId:'owner',channelWebhookToken:'token',channelWebhookAesKey:'aes'})) h.element(id).value=value;
  const form={reportValidity:()=>true,querySelectorAll:()=>[]}; h.context.form=form;
  await vm.runInContext('channelsAdmin.save({preventDefault(){},currentTarget:form})',h.context);
  const p=h.requests[0].payload;
  assert.equal(p.channel_type,'wecom_app'); assert.equal(p.transport,'webhook');
  assert.equal(p.webhook_token,'token'); assert.equal(p.webhook_aes_key,'aes');
  assert.equal(p.settings.wecom_app.application_id,'app');
  assert.equal('bot_id' in p,false); assert.equal('secret' in p,false);
  assert.equal(h.element('channelAccountModal').closed,true);
  assert.equal(h.context.window.opened,undefined);
  assert.equal(h.element('channelCreatedModal').opened,true);
  assert.equal(h.element('channelCreatedAddress').hidden,false);
});
test('Editing omits existing masked and empty Webhook secrets',async()=>{
  const h=harness();
  vm.runInContext('channelAdminState.editingId="existing";channelAdminState.accounts=[{id:"existing",settings:{wecom_app:{application_id:"app"}}}];channelAdminState.applications=[{id:"app"}];',h.context);
  h.element('channelWebhookApplicationId').value='app'; h.element('channelWebhookToken').value='***';h.element('channelWebhookToken').dataset.maskedValue='***';
  h.context.form={reportValidity:()=>true,querySelectorAll:()=>[]};
  await vm.runInContext('channelsAdmin.save({preventDefault(){},currentTarget:form})',h.context);
  assert.equal('webhook_token' in h.requests[0].payload,false);
  assert.equal('webhook_aes_key' in h.requests[0].payload,false);
  assert.equal(h.element('channelAccountModal').closed,true);
  assert.equal(h.element('channelCreatedModal').opened,undefined);
});

test('Creation result uses current origin instead of a stale server URL',()=>{
  const h=harness();
  vm.runInContext('showChannelCreated({id:"abc",webhook_url:"https://hooks.example/channels/abc"})',h.context);
  assert.equal(h.element('channelCreatedUrl').value,'https://alpha.deepfos.com/webhooks/agent/channel/abc');
  assert.equal(h.element('channelCreatedAddress').hidden,false);
});

test('Copy-and-close retains creation dialog on failure and closes only after successful write', async()=>{
  const h=harness(); h.ready.at(-1)();
  vm.runInContext('showChannelCreated({id:"copy-fixture"})', h.context);
  const finish=h.listeners.get('finishChannelCreated:click');
  h.context.navigator.clipboard.writeText=async()=>{throw Error('permission denied')};
  await finish();
  assert.equal(h.element('channelCreatedModal').closed, undefined);
  let copied; h.context.navigator.clipboard.writeText=async value=>{copied=value};
  await finish();
  assert.equal(copied,'https://alpha.deepfos.com/webhooks/agent/channel/copy-fixture');
  assert.equal(h.element('channelCreatedModal').closed,true);
});
