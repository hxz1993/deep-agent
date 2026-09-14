import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../../../console/configuration/wecom-applications.js', import.meta.url), 'utf8');
const channelSource = readFileSync(new URL('../../../console/configuration/channels.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../../../console/configuration-template.html', import.meta.url), 'utf8');
const fixture = { id: 'app-1', name: '成员应用', corp_id: 'corp-a', wecom_agent_id: '100', app_secret_masked: 'sample******last', secret_set: true, channel_count: 2, channels: [{ id: 'channel-a', name: '测试机器人' }, { id: 'channel-b', name: '业务助手' }] };
function harness(custom) {
  const nodes = new Map(), requests = [], timers = new Map(), feedback = [], ready = [];
  let timerId = 0;
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, { value: '', innerHTML: '', textContent: '', dataset: {}, attributes: {}, listeners: {}, classList: { toggle() {} },
      addEventListener(type, cb) { this.listeners[type] = cb; },
      querySelectorAll() { return []; }, querySelector() { return null; },
      getAttribute(key) { return this.attributes[key]; }, setAttribute(key, value) { this.attributes[key] = value; },
      showModal() { this.open = true; }, close() { this.open = false; }, reportValidity() { return true; },
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    window: { addEventListener() {} },
    document: { getElementById: get, querySelectorAll() { return []; }, addEventListener(type, cb) { if (type === 'DOMContentLoaded') ready.push(cb); } },
    AbortController, Map, Set, Number, Object, Array, String, Promise,
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; }, clearTimeout(id) { timers.delete(id); },
    escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
    formatDate: value => value,
    apiFetch: async (path, options = {}) => {
      const request = { path, ...options, payload: options.body ? JSON.parse(options.body) : undefined }; requests.push(request);
      if (custom) { const result = custom(request); if (result !== undefined) return result; }
      if (path.endsWith('/cache')) return { total: 2, pending: 1, earliest_created_at: '2026-09-01', earliest_synced_at: '2026-09-12' };
      if (path.endsWith('/wecom-applications')) return [fixture];
      return fixture;
    },
    AdminFormFeedback: { clear() {}, show(form, message) { feedback.push(message); } },
    confirmAction: async () => true, showToast() {},
  });
  vm.runInContext(source, context);
  ready.forEach(cb => cb());
  return { admin: context.window.wecomApplicationsAdmin, context, get, requests, timers, feedback };
}
async function select(h) { await h.admin.refresh(); await h.admin.select('app-1'); }

test('渠道仅增加标题同行切换和应用主从视图，不增加成员表', () => {
  const section = template.split('id="panel-channels">')[1].split('<!-- MCP panel -->')[0];
  assert.match(section, /channelAccountActions[\s\S]*newWecomApplicationBtn[\s\S]*channel-view-switch/);
  assert.match(section, /data-channel-view="accounts"/);
  assert.match(section, /data-channel-view="applications"/);
  assert.match(section, /id="wecomApplicationList"[\s\S]*id="wecomApplicationDetail"/);
  assert.doesNotMatch(source, /<table|\/users\b|\/members\b/);
});

test('旧渠道设置默认关闭，修改保留未知 settings 和企微扩展值', () => {
  const context = vm.createContext({ window: {}, document: { addEventListener() {} } });
  vm.runInContext(channelSource, context);
  const result = vm.runInContext('channelUserDetailsSettings({custom: 4, wecom: {other: "keep", application_id: "old"}}, false, "old")', context);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { custom: 4, wecom: { other: 'keep', user_details_enabled: false, application_id: 'old' } });
  assert.match(channelSource, /settings\?\.wecom\?\.user_details_enabled === true/);
  assert.match(channelSource, /enabled && !channelAdminState\.applications\.some/);
  assert.match(template, /id="channelUserDetailsEnabled"[^>]*aria-checked="false"/);
});

test('应用编辑只读身份，安全掩码回显，未修改 Secret 不提交', async () => {
  const h = harness(request => request.method === 'PUT' ? fixture : undefined);
  await select(h); h.admin.openEdit();
  assert.equal(h.get('wecomApplicationCorpId').readOnly, true);
  assert.equal(h.get('wecomApplicationAgentId').readOnly, true);
  assert.equal(h.get('wecomApplicationSecret').value, fixture.app_secret_masked);
  for (const value of [fixture.app_secret_masked, '', '  ']) {
    h.admin.openEdit(); h.get('wecomApplicationSecret').value = value;
    await h.admin.save({ preventDefault() {}, currentTarget: h.get('wecomApplicationForm') });
    assert.deepEqual(h.requests.findLast(request => request.method === 'PUT').payload, { name: fixture.name });
  }
});

test('替换凭据只提交新值，新建要求应用身份且可取消', async () => {
  const h = harness(request => request.method === 'PUT' ? fixture : undefined);
  await select(h); h.admin.openEdit();
  h.get('wecomApplicationSecret').value = 'replacement-only';
  await h.admin.save({ preventDefault() {}, currentTarget: h.get('wecomApplicationForm') });
  assert.equal(h.requests.findLast(request => request.method === 'PUT').payload.app_secret, 'replacement-only');
  h.admin.openCreate();
  assert.equal(h.get('wecomApplicationCorpId').readOnly, false);
  assert.equal(h.get('wecomApplicationAgentId').readOnly, false);
  assert.equal(h.get('wecomApplicationSecret').required, true);
  const count = h.requests.length;
  h.admin.closeEditor();
  assert.equal(h.requests.length, count);
  assert.equal(h.get('wecomApplicationSecret').value, '');
});

test('读取详情只显示后端掩码并转义名称，不输出原始凭据', async () => {
  const unsafe = { ...fixture, name: '<script>bad</script>', app_secret: 'should-not-render' };
  const h = harness(request => request.path.endsWith('/app-1') ? unsafe : undefined);
  await select(h);
  assert.match(h.get('wecomApplicationDetail').innerHTML, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.match(h.get('wecomApplicationDetail').innerHTML, /sample\*{6}last/);
  assert.doesNotMatch(h.get('wecomApplicationDetail').innerHTML, /should-not-render/);
  assert.match(h.get('wecomApplicationCache').innerHTML, /1 人待更新/);
});

test('重置缓存保留资料，仅调用应用失效并重新读取统计', async () => {
  let pending = 1;
  const h = harness(request => {
    if (request.path.endsWith('/cache/invalidate')) { pending = 2; return { total: 2, pending }; }
    if (request.path.endsWith('/cache')) return { total: 2, pending };
  });
  await select(h); await h.admin.invalidate();
  assert.match(h.get('wecomApplicationCache').innerHTML, /2 人待更新/);
  assert.match(h.get('wecomCacheStatus').textContent, /姓名和 ID 映射保留/);
  assert.equal(h.requests.filter(request => request.method === 'DELETE').length, 0);
  assert.equal(h.requests.filter(request => request.path.endsWith('/cache/invalidate')).length, 1);
});

test('批量刷新运行中忽略重复提交，轮询结束后读取统计', async () => {
  let pending = 1;
  const h = harness(request => {
    if (request.path.endsWith('/cache/refresh')) return { task_id: 'task-1', status: 'running', total: 2, completed: 0 };
    if (request.path.endsWith('/cache/refresh/task-1')) { pending = 0; return { task_id: 'task-1', status: 'completed', total: 2, completed: 2, succeeded: 2, failed: 0 }; }
    if (request.path.endsWith('/cache')) return { total: 2, pending };
  });
  await select(h); await h.admin.refreshCache(); await h.admin.refreshCache();
  assert.equal(h.requests.filter(request => request.path.endsWith('/cache/refresh')).length, 1);
  assert.match(h.get('wecomCacheStatus').textContent, /正在刷新 0 \/ 2/);
  await Array.from(h.timers.values()).find(timer => timer.ms === 1500).fn();
  assert.match(h.get('wecomCacheStatus').textContent, /成功 2，失败 0/);
  assert.match(h.get('wecomApplicationCache').innerHTML, /0 人待更新/);
});

test('任务丢失时结果待确认，不推断刷新成功', async () => {
  const h = harness(request => {
    if (request.path.endsWith('/cache/refresh')) return { task_id: 'lost', status: 'running', total: 2 };
    if (request.path.endsWith('/cache/refresh/lost')) return Promise.reject(new Error('404'));
  });
  await select(h); await h.admin.refreshCache();
  await Array.from(h.timers.values()).find(timer => timer.ms === 1500).fn();
  assert.match(h.get('wecomCacheStatus').textContent, /结果待确认/);
  assert.doesNotMatch(h.get('wecomCacheStatus').textContent, /成功/);
});

test('离开页面中止读取，忽略迟到列表', async () => {
  let release;
  const h = harness(request => request.path.endsWith('/wecom-applications') ? new Promise(resolve => { release = resolve; }) : undefined);
  const loading = h.admin.refresh();
  const signal = h.requests[0].signal;
  h.admin.deactivate(); release([fixture]); await loading;
  assert.equal(signal.aborted, true);
  assert.equal(h.get('wecomApplicationList').innerHTML, '');
});

test('测试连接失败就近显示，成功不代表所有成员有权限', async () => {
  let success = false;
  const h = harness(request => request.path.endsWith('/test-connection') ? { success, message: '当前出口不可信' } : undefined);
  await select(h); await h.admin.test();
  assert.equal(h.get('wecomApplicationStatus').textContent, '当前出口不可信');
  success = true; await h.admin.test();
  assert.match(h.get('wecomApplicationStatus').textContent, /成员查询范围仍取决于应用权限/);
});

test('服务端返回 unknown 任务状态同样显示结果待确认', async () => {
  const h = harness(request => {
    if (request.path.endsWith('/cache/refresh')) return { task_id: 'reset-job', status: 'running', total: 2 };
    if (request.path.endsWith('/cache/refresh/reset-job')) return { task_id: 'reset-job', status: 'unknown', total: 2, succeeded: 1 };
  });
  await select(h); await h.admin.refreshCache();
  await Array.from(h.timers.values()).find(timer => timer.ms === 1500).fn();
  assert.match(h.get('wecomCacheStatus').textContent, /结果待确认/);
  assert.doesNotMatch(h.get('wecomCacheStatus').textContent, /刷新结束/);
});


test('应用标题和边框操作位于单个配置卡外，关联渠道展示名称', async () => {
  const h = harness(); await select(h);
  const html = h.get('wecomApplicationDetail').innerHTML;
  assert.match(html, /wecom-detail-heading[\s\S]*成员应用[\s\S]*class="btn" data-wecom-action="edit"/);
  assert.equal((html.match(/class="wecom-info-card"/g) || []).length, 1);
  assert.doesNotMatch(html, /<h3>企业信息|<h3>应用信息/);
  assert.match(html, /<li>测试机器人<\/li><li>业务助手<\/li>/);
  assert.match(html, /class="field-help"[\s\S]*资料有效期为一周/);
});

test('缓存仅提供重置入口并锚定二次确认', () => {
  assert.match(source, /else askCacheConfirmation\(button, action\)/);
  assert.match(source, /setAttribute\('popover', 'auto'\)/);
  assert.match(source, /trigger\.getBoundingClientRect\(\)/);
  assert.doesNotMatch(source, /<button[^>]*data-wecom-action="refresh"/);
  assert.match(source, /保留姓名和 ID 映射，仅将资料标记为过期/);
  const invalidate = source.split('async invalidate()')[1];
  assert.doesNotMatch(invalidate, /confirmAction/);
});

test('应用整张卡片可选择，首次选首项且刷新保留有效选择', async () => {
  const other = { ...fixture, id: 'app-2', name: '另一应用' };
  const h = harness(request => {
    if (request.path.endsWith('/wecom-applications')) return [fixture, other];
    if (request.path.endsWith('/app-2')) return other;
  });
  await h.admin.refresh();
  assert.match(h.get('wecomApplicationList').innerHTML, /<button type="button" class="provider-list-item wecom-application-list-item active" data-wecom-application="app-1"/);
  assert.doesNotMatch(h.get('wecomApplicationList').innerHTML, /data-wecom-delete|wecom-list-delete/);
  assert.match(h.get('wecomApplicationDetail').innerHTML, /成员应用/);
  await h.admin.select('app-2'); await h.admin.refresh();
  assert.match(h.get('wecomApplicationList').innerHTML, /active" data-wecom-application="app-2"/);
  assert.match(h.get('wecomApplicationDetail').innerHTML, /另一应用/);
});

test('显式清空 Secret 仅改变草稿，保存携带独立清空标记', async () => {
  const h = harness(request => request.method === 'PUT' ? fixture : undefined);
  await select(h); h.admin.openEdit();
  const before = h.requests.length;
  h.get('clearWecomApplicationSecretBtn').listeners.click();
  assert.equal(h.requests.length, before);
  assert.equal(h.get('wecomApplicationSecret').value, '');
  assert.equal(h.get('wecomApplicationSecret').dataset.clearRequested, 'true');
  assert.equal(h.get('wecomApplicationSecretHint').textContent, '保存后删除密钥');
  assert.equal(h.get('clearWecomApplicationSecretBtn').textContent, '保留原密钥');
  await h.admin.save({ preventDefault() {}, currentTarget: h.get('wecomApplicationForm') });
  assert.deepEqual(h.requests.findLast(request => request.method === 'PUT').payload, { name: fixture.name, clear_app_secret: true });
});

test('清空可撤销，输入新值取消清空标记，关闭编辑器不保存', async () => {
  const h = harness(request => request.method === 'PUT' ? fixture : undefined);
  await select(h); h.admin.openEdit();
  const input = h.get('wecomApplicationSecret');
  const clear = () => h.get('clearWecomApplicationSecretBtn').listeners.click();
  clear(); clear();
  assert.equal(input.value, fixture.app_secret_masked);
  assert.equal(input.dataset.clearRequested, 'false');
  clear(); input.value = 'replacement-fixture'; input.listeners.input({ target: input });
  assert.equal(input.dataset.clearRequested, 'false');
  await h.admin.save({ preventDefault() {}, currentTarget: h.get('wecomApplicationForm') });
  assert.deepEqual(h.requests.findLast(request => request.method === 'PUT').payload, { name: fixture.name, app_secret: 'replacement-fixture' });
  h.admin.openEdit(); clear();
  const before = h.requests.length;
  h.admin.closeEditor();
  assert.equal(h.requests.length, before);
  assert.equal(input.dataset.clearRequested, 'false');
});

test('应用与缓存反馈分别紧跟自己的操作栏，缓存失败不占连接状态', async () => {
  const h = harness(request => request.path.endsWith('/cache/invalidate') ? Promise.reject(new Error('重置失败')) : undefined);
  await select(h); await h.admin.test();
  const previous = h.get('wecomApplicationStatus').textContent;
  await h.admin.invalidate();
  assert.equal(h.get('wecomCacheStatus').textContent, '重置失败');
  assert.equal(h.get('wecomApplicationStatus').textContent, previous);
  const html = h.get('wecomApplicationDetail').innerHTML;
  assert.match(html, /<\/header>\s*<p id="wecomApplicationStatus"[\s\S]*<section class="wecom-info-card"/);
  assert.match(html, /data-wecom-action="invalidate"[\s\S]*id="wecomCacheStatus"[\s\S]*id="wecomApplicationCache"/);
});
