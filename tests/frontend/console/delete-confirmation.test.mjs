import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const core = await readFile(new URL('../../../console/configuration/core.js', import.meta.url), 'utf8');
const agents = await readFile(new URL('../../../console/configuration/agents.js', import.meta.url), 'utf8');
function load() {
  const context = vm.createContext({ window: {}, document: { addEventListener() {} } });
  vm.runInContext(core, context);
  return context;
}
const conflict = (token) => Object.assign(new Error('confirm'), { status: 409, detail: {
  code: 'delete_confirmation_required', impact: { session_count: 3 }, confirmation_token: token,
} });
const options = { title: '确认', describeImpact: impact => String(impact.session_count), confirmLabel: '删除' };

test('删除范围变化必须重新确认，取消后不再发送 DELETE', async () => {
  const ui = load(), calls = [], dialogs = [];
  ui.apiFetch = async (_, request) => {
    calls.push(request);
    throw conflict(calls.length === 1 ? 'first' : 'changed');
  };
  ui.confirmAction = async detail => { dialogs.push(detail); return dialogs.length === 1; };
  assert.equal(await ui.deleteWithImpactConfirmation('/agent', options), false);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].body, undefined);
  assert.deepEqual(JSON.parse(calls[1].body), { confirmation_token: 'first' });
  assert.equal(dialogs.length, 2);
  assert.equal(dialogs[0].tone, 'danger');
});

test('只有明确确认后才携带服务端 token，非确认冲突原样报错', async () => {
  const ui = load(); let count = 0;
  ui.apiFetch = async (_, request) => {
    if (++count === 1) throw conflict('proof');
    assert.equal(JSON.parse(request.body).confirmation_token, 'proof');
  };
  ui.confirmAction = async () => true;
  assert.equal(await ui.deleteWithImpactConfirmation('/agent', options), true);
  ui.apiFetch = async () => { throw Object.assign(new Error('渠道引用'), { status: 409 }); };
  ui.confirmAction = async () => { assert.fail('关联阻断不能被误当成删除授权'); };
  await assert.rejects(ui.deleteWithImpactConfirmation('/agent', options), /渠道引用/);
});

test('API 错误保留确认计数及状态，503不全部误报为数据库未配置', async () => {
  const ui = load();
  const detail = conflict('proof').detail;
  ui.daFetch = async () => ({ ok: false, status: 409, json: async () => ({ detail }) });
  await assert.rejects(ui.apiFetch('/agent'), error => error.status === 409 && error.detail === detail);
  ui.daFetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: '等待运行停止超时' }) });
  await assert.rejects(ui.apiFetch('/agent'), /等待运行停止超时/);
});

test('已关联但缺失的 Skill 仍可关闭解除引用', () => {
  const ui = load(), container = { innerHTML: '' }, counter = {};
  ui.document.getElementById = id => id === 'agentSkillChoices' ? container : counter;
  ui.document.querySelectorAll = () => [];
  ui.escapeHtml = value => String(value ?? '');
  vm.runInContext(agents, ui);
  ui.window.agentsAdmin.renderSkillChoices(['missing-skill']);
  const input = container.innerHTML.match(/<input[^>]+name="agentSkill"[^>]+>/)[0];
  assert.match(input, /checked/);
  assert.match(input, /data-missing="true"/);
  assert.doesNotMatch(input, /\sdisabled(?:\s|\/?>)/);
});
