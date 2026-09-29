import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const source = await readFile(
  new URL("../../../console/configuration/channels.js", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(new URL("../../../console/console.css", import.meta.url), "utf8");
const sharedPrimitives = await readFile(new URL("../../../console/ui-primitives.css", import.meta.url), "utf8");

test("配置中心提供企微渠道页和完整运行状态", () => {
  assert.match(indexHtml, /data-tab="channels"[\s\S]*?<span>渠道<\/span>/);
  assert.match(templateHtml, /id="panel-channels"/);
  assert.match(templateHtml, /id="channelRuntimeBanner"/);
  assert.match(templateHtml, /channel-title-row[\s\S]*id="channelRuntimeBanner"[\s\S]*id="refreshChannelsBtn"/);
  assert.match(templateHtml, /连接外部聊天平台，将消息交给绑定的 Agent 处理。/);
  assert.match(templateHtml, /网关：读取中/);
  assert.match(templateHtml, /id="refreshChannelsBtn" title="刷新渠道网关状态"[\s\S]*data-lucide="refresh-cw"/);
  assert.doesNotMatch(templateHtml, />刷新状态<\/button>/);
  assert.doesNotMatch(templateHtml, /channel-runtime-banner/);
  assert.match(source, /not_deployed:\s*"未部署"/);
  assert.match(source, /unreachable:\s*"运行时不可达"/);
  assert.match(source, /connected:\s*"已连接"/);
  assert.match(source, /conflict:\s*"连接冲突"/);
  assert.match(source, /textContent = `网关：\$\{stateLabel\}`/);
  assert.match(source, /connected: "运行中"/);
});

function commandHarness(fetchCatalog) {
  const elements = {
    channelHelpModal: { open: false, showModal() { this.open = true; } },
    channelHelpMessagesPanel: { hidden: false },
    channelHelpCommandsPanel: { hidden: true },
    channelCommandsContent: { innerHTML: "" },
    retryChannelCommandsBtn: { hidden: false },
  };
  const requests = [];
  const context = vm.createContext({
    window: {},
    document: { addEventListener() {}, querySelectorAll() { return []; }, getElementById(id) { return elements[id]; } },
    apiFetch(path) { requests.push(path); return fetchCatalog(); },
    escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); },
  });
  vm.runInContext(source, context);
  return { admin: context.window.channelsAdmin, elements, requests, context };
}

test("渠道操作独占第二行，一级切换与网关胶囊保留标题行", () => {
  const panel = templateHtml.split('id="panel-channels">')[1].split('<div class="table-wrap')[0];
  const [title, actions] = panel.split('class="channel-content-toolbar"');
  assert.match(title, /channelRuntimeBanner[\s\S]*refreshChannelsBtn/);
  assert.match(title, /class="panel-subtitle" id="channelViewDescription"/);
  assert.doesNotMatch(title, /channelHelpBtn/);
  assert.match(title, /channel-view-switch/);
  assert.match(actions, /newChannelBtn[\s\S]*channelHelpBtn/);
  assert.match(styles, /\.channel-help-btn\s*\{[^}]*border: 0;[^}]*background: transparent;/);
});

test("渠道标题沿用目录标题基线，状态胶囊不撑高标题行", () => {
  assert.match(sharedPrimitives, /#configurationHost #panel-channels \.panel-title-copy\s*\{\s*display: block;/);
  assert.match(sharedPrimitives, /#configurationHost \.channel-title-row \.channel-runtime-chip\s*\{\s*margin-block: -4px;/);
});

test("帮助默认展示支持消息，切换快捷命令才读取目录并转义内容", async () => {
  const { admin, elements, requests } = commandHarness(async () => ({
    commands: [{ command: "/help", description: "说明 <script>" }],
    notes: ["群聊影响 <所有成员>"],
  }));
  admin.openHelp();
  assert.equal(elements.channelHelpModal.open, true);
  assert.equal(elements.channelHelpMessagesPanel.hidden, false);
  assert.equal(elements.channelHelpCommandsPanel.hidden, true);
  assert.equal(requests.length, 0);
  await admin.selectHelpTab("commands");
  assert.equal(elements.channelHelpMessagesPanel.hidden, true);
  assert.equal(elements.channelHelpCommandsPanel.hidden, false);
  assert.deepEqual(requests, ["/api/v1/channel-commands"]);
  assert.equal(elements.channelHelpModal.open, true);
  assert.match(elements.channelCommandsContent.innerHTML, /<code>\/help<\/code>/);
  assert.match(elements.channelCommandsContent.innerHTML, /说明 &lt;script&gt;/);
  assert.doesNotMatch(elements.channelCommandsContent.innerHTML, /<script>|<button|执行/);
  assert.equal(elements.retryChannelCommandsBtn.hidden, true);
  assert.match(templateHtml, /id="channelHelpCommandsTab"[\s\S]*快捷命令/);
  assert.match(templateHtml, /id="channelHelpModal" aria-labelledby="channelHelpModalTitle"/);
  assert.doesNotMatch(source, /command: "\/(help|new|stop|status|whoami)"/);
});

test("快捷命令请求失败就近展示错误并支持重试", async () => {
  let attempt = 0;
  const { admin, elements } = commandHarness(async () => {
    if (!attempt++) throw new Error("HTTP 503：暂不可用");
    return { commands: [{ command: "/help", description: "帮助" }], notes: [] };
  });
  await admin.loadCommands();
  assert.match(elements.channelCommandsContent.innerHTML, /role="alert".*HTTP 503：暂不可用/);
  assert.equal(elements.retryChannelCommandsBtn.hidden, false);
  await admin.loadCommands();
  assert.equal(elements.retryChannelCommandsBtn.hidden, true);
  assert.match(elements.channelCommandsContent.innerHTML, /\/help/);
});

test("网关状态与机器人认证状态分开表达", () => {
  const { admin, context, elements } = commandHarness(async () => ({}));
  const label = { textContent: "" };
  elements.channelRuntimeBanner = { dataset: {}, querySelector() { return label; } };
  for (const [runtime, expected, state] of [
    [{ deployed: false, reachable: false }, "网关：未部署", "not_deployed"],
    [{ deployed: true, reachable: false }, "网关：不可达", "unreachable"],
    [{ deployed: true, reachable: true, state: "connected", accounts: [{ state: "failed" }] }, "网关：运行中", "connected"],
  ]) {
    vm.runInContext(`channelAdminState.runtime = ${JSON.stringify(runtime)}`, context);
    admin.renderRuntime();
    assert.equal(label.textContent, expected);
    assert.equal(elements.channelRuntimeBanner.dataset.state, state);
  }
});

test("渠道表单绑定Agent且Secret只展示脱敏值并支持留空保留", () => {
  assert.match(templateHtml, /id="channelAgentId"/);
  assert.match(templateHtml, /<label for="channelSessionUserId">会话关联用户<\/label>/);
  assert.match(templateHtml, /type="text" id="channelSessionUserId" maxlength="128" required/);
  assert.doesNotMatch(templateHtml, /channelSessionUserHint/);
  assert.match(templateHtml, /id="fillChannelCurrentUser">填充我的 ID/);
  assert.match(source, /session_user_id: document.getElementById\("channelSessionUserId"\).value.trim\(\)/);
  assert.match(source, /channelSessionUserId"\).value = account.session_user_id/);
  assert.match(templateHtml, /id="channelBotId"/);
  assert.match(templateHtml, /渠道名称/);
  assert.doesNotMatch(templateHtml, /渠道类型/);
  assert.match(templateHtml, /id="channelBotId"[\s\S]*<span[^>]*>Secret<\/span>[\s\S]*id="channelSecret"/);
  assert.doesNotMatch(templateHtml, /channel-form-wide/);
  assert.match(templateHtml, /<input type="password" id="channelSecret"/);
  assert.doesNotMatch(templateHtml, /0\.7\.0 按普通字符串保存/);
  assert.doesNotMatch(templateHtml, /id="channelWelcomeMessage"|id="channelEnabled"/);
  assert.doesNotMatch(templateHtml, /owner_user_id|platform_token|cookie/i);
  assert.doesNotMatch(templateHtml, /channelAppId|channelSpaceId|channelLanguage|欢迎语/);
  assert.doesNotMatch(source, /space_id|language|welcome/i);
  assert.match(source, /setChannelSecretEditor\(\{ maskedValue: account\.secret_masked, secretSet: account\.secret_set \}\)/);
  assert.match(source, /channelAccountModalTitle"\)\.textContent = account\.name/);
  assert.doesNotMatch(source, /编辑企微渠道 ·/);
  assert.doesNotMatch(source, /account\.secret\b/);
  assert.match(source, /if \(secret && secret !== secretInput\.dataset\.maskedValue\) payload\.secret = secret/);
  assert.doesNotMatch(templateHtml, /clearChannelSecret|channelSecretClear/);
  assert.doesNotMatch(source, /clear_secret|clearSecret|clearRequested/);
  assert.match(source, /\/api\/v1\/channel-accounts/);
  assert.match(source, /\/test-connection/);
});

function secretEditorHarness() {
  const elements = new Map();
  const ready = [];
  const requests = [];
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", dataset: {}, listeners: {}, attributes: {}, open: false, selected: false,
      classList: { toggle() {} }, querySelectorAll() { return []; },
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      closest() { return this; }, append(child) { this.child = child; },
      getAttribute(key) { return this.attributes[key]; },
      addEventListener(type, listener) { this.listeners[type] = listener; },
      showModal() { this.open = true; }, close() { this.open = false; },
      select() { this.selected = true; }, reportValidity() { return true; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    window: { location: {origin:"http://127.0.0.1:8000"}, createChannelRegistration: () => ({ reset() {}, start() {} }) },
    document: {
      addEventListener(type, listener) { if (type === "DOMContentLoaded") ready.push(listener); },
      getElementById: getElement,
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    apiFetch: async (path, options) => { if (!options?.body) return []; requests.push({ path, ...options, payload: JSON.parse(options.body) }); },
    AdminFormFeedback: { clear() {}, show() { assert.fail("unexpected save failure"); } },
    escapeHtml: (value) => String(value ?? ""),
    showToast() {},
    refreshAfterSuccessfulMutation: async (_message, refresh) => { try { await refresh(); } catch {} },
  });
  vm.runInContext(source, context);
  vm.runInContext(`channelAdminState.accounts = [
    { id: "channel-1", name: "示例渠道", agent_id: "agent-1", bot_id: "bot-1", session_user_id: "user-1", secret_set: true, secret_masked: "sample********1234" },
    { id: "channel-2", name: "另一渠道", agent_id: "agent-1", bot_id: "bot-2", session_user_id: "user-2", secret_set: true, secret_masked: "other-********5678" }
  ]; channelAdminState.agents = [{ id: "agent-1", name: "示例 Agent" }];`, context);
  ready.forEach((listener) => listener());
  const admin = context.window.channelsAdmin;
  admin.refresh = async () => {};
  return { admin, getElement, requests, context };
}

test("渠道Secret回显安全掩码，聚焦全选并由公共控件管理显示", () => {
  const { admin, getElement } = secretEditorHarness();
  admin.openEdit("channel-1");
  const secret = getElement("channelSecret");
  assert.equal(secret.value, "sample********1234");
  assert.equal(secret.required, false);
  secret.listeners.focus({ currentTarget: secret });
  assert.equal(secret.selected, true);
  secret.value = "replacement-fixture";
  admin.openEdit("channel-2");
  assert.equal(secret.value, "other-********5678");
  admin.openCreate();
  assert.equal(secret.value, "");
  assert.equal(secret.dataset.maskedValue, "");
  assert.equal(secret.required, true);
});

test("保存渠道时掩码和留空均不提交Secret，只有重新填写才更新", async () => {
  const { admin, getElement, requests } = secretEditorHarness();
  const form = getElement("channelAccountForm");
  const submit = () => admin.save({ preventDefault() {}, currentTarget: form });
  for (const editedValue of [null, "", "   ", "replacement-fixture"]) {
    admin.openEdit("channel-1");
    if (editedValue !== null) getElement("channelSecret").value = editedValue;
    await submit();
    const request = requests.at(-1);
    assert.equal(request.method, "PUT");
    assert.equal(request.path, "/api/v1/channel-accounts/channel-1");
    if (editedValue === "replacement-fixture") assert.equal(request.payload.secret, editedValue);
    else assert.equal(Object.hasOwn(request.payload, "secret"), false);
    assert.equal(Object.hasOwn(request.payload, "clear_secret"), false);
  }
  admin.openEdit("channel-1");
  assert.equal(getElement("channelSecret").value, "sample********1234");
});

test("渠道启停只在列表使用Switch切换", () => {
  assert.match(source, /data-toggle-channel=/);
  assert.match(source, /role="switch"/);
  assert.match(source, /channel-enabled-cell[\s\S]*data-toggle-channel=[\s\S]*channel-row-actions compact-icon-actions[\s\S]*data-channel-test=[\s\S]*data-channel-delete=/);
  assert.match(source, /async toggleEnabled\(accountId, button\)/);
  assert.match(source, /JSON\.stringify\(\{ enabled: !account\.enabled \}\)/);
  assert.doesNotMatch(templateHtml, /<th>启用<\/th>/);
});

test("渠道列表使用单行类型、行点击编辑与统一纯图标操作", () => {
  assert.match(source, /channelPlatforms\[account.channel_type\]\?\.name/);
  assert.doesNotMatch(source, /channel-account-code/);
  assert.match(source, /<td><strong>\$\{escapeHtml\(account\.name\)\}<\/strong><\/td>\s*<td class="channel-type-cell">/);
  assert.match(source, /class="channel-list-row" data-open-channel=/);
  assert.match(source, /if \(event\.target\.closest\("a, button"\)\) return/);
  assert.match(source, /\.channel-row-actions[\s\S]*event\.stopPropagation\(\)/);
  assert.match(source, /data-channel-test=[\s\S]*title="测试连接" aria-label="测试连接"/);
  assert.match(source, /data-channel-delete=[\s\S]*title="删除渠道" aria-label="删除渠道"/);
  assert.doesNotMatch(source, /data-channel-edit=/);
  assert.doesNotMatch(source, /data-channel-test[^>]*>[\s\S]*?测试<\/button>/);
  assert.match(styles, /\.compact-icon-actions \.icon-btn\s*\{[\s\S]*width: 28px;[\s\S]*height: 28px;[\s\S]*padding: 0;[\s\S]*border: 0;/);
  assert.match(styles, /\.channel-enabled-cell \.model-enabled-switch\.on\s*\{\s*background: var\(--accent\);/);
});

test("渠道布局包含窄屏适配且不引入第二个页面结构", () => {
  assert.match(styles, /\.channel-account-modal/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*\.channel-form-grid/);
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
});

test("帮助两页共用固定尺寸，长说明仅面板滚动", () => {
  assert.match(styles, /dialog\.console-detail-dialog\.modal\.channel-help-modal\s*\{[^}]*height: min\(800px, calc\(100dvh - 32px\)\);[^}]*min-height: 0;/);
  assert.match(styles, /\.channel-help-panel\s*\{[^}]*min-height: 0;[^}]*overflow-y: auto;/);
});


test("新建渠道先选择企微，支持键盘和外部关闭且不提前创建", () => {
  const { admin, context, elements } = commandHarness(async () => ({}));
  const documentEvents = {};
  let opened = 0;
  const element = () => ({ hidden: true, attributes: {}, listeners: {},
    addEventListener(type, listener) { this.listeners[type] = listener; },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus() { this.focused = true; },
    contains(target) { return target === this; },
  });
  const trigger = elements.newChannelBtn = element();
  const menu = elements.newChannelMenu = element();
  const option = elements.createWecomChannelBtn = element();
  menu.querySelectorAll = () => [option];
  context.document.addEventListener = (type, listener) => { documentEvents[type] = listener; };
  admin.openCreate = () => { opened += 1; };
  vm.runInContext("bindChannelCreateMenu()", context);
  trigger.listeners.click();
  assert.equal(opened, 0);
  assert.equal(menu.hidden, false);
  assert.equal(trigger.attributes["aria-expanded"], "true");
  documentEvents.keydown({ key: "Escape", preventDefault() {} });
  assert.equal(menu.hidden, true);
  assert.equal(trigger.focused, true);
  trigger.listeners.keydown({ key: "ArrowDown", preventDefault() {} });
  assert.equal(option.focused, true);
  option.listeners.click();
  assert.equal(opened, 1);
  assert.equal(menu.hidden, true);
  trigger.listeners.click();
  documentEvents.click({ target: {} });
  assert.equal(menu.hidden, true);
  assert.match(templateHtml, /id="createWecomChannelBtn" role="menuitem"[^\n]*<span>企微<\/span>[^\n]*WebSocket/);
});


test("渠道Key草稿保留输入供公共眼睛查看，保存发送原值，显式清空不回传掩码", async () => {
  const { admin, getElement, requests } = secretEditorHarness();
  admin.openEdit("channel-1");
  const key = getElement("channelDeepfosApiKey");
  key.value = "demo-only-key-123456";
  key.listeners.input();
  assert.equal(key.value, "demo-only-key-123456");
  const submit = () => admin.save({ preventDefault() {}, currentTarget: getElement("channelAccountForm") });
  await submit();
  assert.equal(requests.at(-1).payload.settings.deepfos.api_key, "demo-only-key-123456");
  getElement("clearChannelDeepfosApiKeyBtn").listeners.click();
  assert.equal(key.value, "");
  await submit();
  assert.equal(requests.at(-1).payload.settings.deepfos.clear_api_key, true);
  assert.equal(requests.at(-1).payload.settings.deepfos.api_key, undefined);
  admin.openEdit("channel-2");
  await submit();
  assert.equal(requests.at(-1).payload.settings.deepfos.clear_api_key, undefined);
  assert.equal(requests.at(-1).payload.settings.deepfos.api_key, undefined);
});

test("应用关联默认关闭，开启显示必填配置，关闭保留内容且隐藏并禁用", async () => {
  const { admin, getElement, requests } = secretEditorHarness();
  admin.openEdit("channel-1");
  const toggle = getElement("channelDeepfosEnabled");
  const input = getElement("channelDeepfosAppId");
  const field = getElement("channelDeepfosAppField");
  const switchToggle = () => toggle.listeners.click({ currentTarget: toggle });
  assert.equal(toggle.getAttribute("aria-checked"), "false");
  assert.equal(field.hidden, true);
  assert.equal(input.disabled, true);
  switchToggle();
  assert.equal(field.hidden, false);
  assert.equal(input.required, true);
  assert.equal(input.disabled, false);
  input.value = "fixture005";
  switchToggle();
  assert.equal(field.hidden, true);
  assert.equal(input.required, false);
  assert.equal(input.disabled, true);
  assert.equal(input.value, "fixture005");
  const submit = () => admin.save({ preventDefault() {}, currentTarget: getElement("channelAccountForm") });
  await submit();
  assert.deepEqual(requests.at(-1).payload.settings.deepfos, { enabled: false, app_id: "fixture005" });
  switchToggle();
  await submit();
  assert.deepEqual(requests.at(-1).payload.settings.deepfos, { enabled: true, app_id: "fixture005" });
  assert.equal(getElement("channelUserDetailsEnabled").getAttribute("aria-checked"), "false");
  admin.openCreate();
  assert.equal(toggle.getAttribute("aria-checked"), "false");
  assert.equal(input.value, "");
});

function userQueryHarness(fetchProfile) {
  const listeners = {};
  const element = (value = "") => ({ value, hidden: true, innerHTML: "", dataset: {}, attributes: {},
    addEventListener() {}, setAttribute(name, value) { this.attributes[name] = value; }, focus() {} });
  const elements = { channelSessionUserId: element("4"), channelSessionUserResult: element(),
    queryChannelSessionUserBtn: element(), channelAccountModal: { ...element(), open: true } };
  const requests = [];
  const context = vm.createContext({ AbortController, window: {}, document: {
    addEventListener(name, callback) { listeners[name] = callback; }, getElementById(id) { return elements[id]; }
  }, apiFetch(path, options) { requests.push({ path, options }); return fetchProfile(path, options); },
  escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); } });
  vm.runInContext(source, context);
  return { elements, requests, query: () => vm.runInContext("queryChannelSessionUser()", context),
    clear: () => vm.runInContext("clearChannelUserQuery()", context) };
}

test("会话关联用户仅手动查询且通过浮层展示资料，不影响保存规则", async () => {
  const h = userQueryHarness(async () => ({user: {user_id: "4", resolution: "resolved", username: "<admin>", email: "a@example.com"}}));
  assert.equal(h.requests.length, 0);
  await h.query();
  assert.equal(h.requests[0].path, "/api/v1/diagnostics/sessions/user-profile?user_id=4");
  assert.match(h.elements.channelSessionUserResult.innerHTML, /&lt;admin&gt;.*a@example.com/);
  assert.equal(h.elements.channelSessionUserResult.dataset.state, "resolved");
  assert.match(styles, /\.channel-user-query-result\s*\{\s*position: absolute;/);
  assert.match(templateHtml, /type="button"[^>]*id="queryChannelSessionUserBtn"/);
});

test("查询未命中、服务失败和认证失败分别展示", async () => {
  for (const [result, expected, state] of [
    [{user: {user_id: "4", resolution: "missing"}}, /查不到这个用户/, "missing"],
    [{user: {user_id: "4", resolution: "unavailable"}}, /查询暂不可用/, "error"],
    [Object.assign(new Error("secret"), {status: 401}), /登录已失效/, "error"],
    [Object.assign(new Error("secret"), {status: 403}), /无权查询/, "error"],
  ]) {
    const h = userQueryHarness(async () => { if (result instanceof Error) throw result; return result; });
    await h.query();
    assert.match(h.elements.channelSessionUserResult.innerHTML, expected);
    assert.equal(h.elements.channelSessionUserResult.dataset.state, state);
    assert.doesNotMatch(h.elements.channelSessionUserResult.innerHTML, /secret/);
  }
});

test("改值或关闭时清除并中止查询，迟到结果不覆盖下一个账号", async () => {
  let finish;
  const h = userQueryHarness(() => new Promise(resolve => { finish = resolve; }));
  const pending = h.query();
  assert.equal(h.elements.channelSessionUserResult.dataset.state, "loading");
  h.elements.channelSessionUserId.value = "another";
  h.clear();
  assert.equal(h.requests[0].options.signal.aborted, true);
  finish({user: {user_id: "4", resolution: "resolved", username: "old"}});
  await pending;
  assert.equal(h.elements.channelSessionUserResult.hidden, true);
  assert.equal(h.elements.channelSessionUserResult.innerHTML, "");
  assert.match(source, /channelSessionUserId"\)\?\.addEventListener\("input", clearChannelUserQuery\)/);
  assert.match(source, /addEventListener\("close", \(\) => \{\s*clearChannelUserQuery\(\)/);
});


test("飞书手填复用表单且不请求企微应用，提交独立渠道类型", async () => {
  const { admin, getElement, requests, context } = secretEditorHarness();
  admin.openCreate("feishu");
  assert.equal(getElement("channelBotId").disabled, true);
  vm.runInContext('setChannelRegistrationMode("manual")', context);
  assert.equal(getElement("channelAccountModalTitle").textContent, "新建飞书渠道");
  assert.equal(getElement("channelBotIdLabel").textContent, "App ID");
  assert.equal(getElement("channelWecomMemberToggle").hidden, false);
  assert.equal(getElement("channelMemberTab").hidden, false);
  assert.equal(getElement("channelWecomApplicationId").required, false);
  getElement("channelBotId").value = "cli_demo";
  getElement("channelSecret").value = "fixture-app-secret";
  getElement("channelName").value = "飞书助手";
  getElement("channelSessionUserId").value = "user-1";
  await admin.save({ preventDefault() {}, currentTarget: getElement("channelAccountForm") });
  const payload = requests.at(-1).payload;
  assert.equal(payload.channel_type, "feishu"); assert.equal(payload.bot_id, "cli_demo");
  assert.equal(payload.secret, "fixture-app-secret"); assert.equal(payload.enabled, false);
  assert.equal(payload.settings.feishu.require_mention, true);
  assert.equal(payload.settings.feishu.user_details_enabled, false);
  assert.equal(Object.hasOwn(payload.settings, "wecom"), false);
});

test("飞书扫码保存只提交授权句柄，不混入手填凭据", async () => {
  const { admin, getElement, requests, context } = secretEditorHarness();
  admin.openCreate("feishu");
  vm.runInContext('setChannelRegistrationMode("scan"); channelRegistration.authorizedId = "registration-ready";', context);
  getElement("channelBotId").value = "stale-cli";
  getElement("channelSecret").value = "stale-secret";
  assert.equal(getElement("channelBotId").disabled, true);
  assert.equal(getElement("channelSecret").required, false);
  await admin.save({ preventDefault() {}, currentTarget: getElement("channelAccountForm") });
  const payload = requests.at(-1).payload;
  assert.equal(payload.registration_id, "registration-ready");
  assert.equal(Object.hasOwn(payload, "bot_id"), false);
  assert.equal(Object.hasOwn(payload, "secret"), false);
});

test("从飞书切回企微恢复原凭据和应用字段语义", () => {
  const { admin, getElement, context } = secretEditorHarness();
  admin.openCreate("feishu"); admin.openCreate();
  vm.runInContext('setChannelRegistrationMode("manual");', context);
  assert.equal(getElement("channelBotIdLabel").textContent, "Bot ID");
  assert.equal(getElement("channelRegistrationSetup").hidden, false);
  assert.equal(getElement("channelWecomMemberToggle").hidden, false);
  assert.equal(getElement("channelBotId").disabled, false);
  assert.equal(getElement("channelSecret").required, true);
});

test("飞书切换接入方式保留已生成的授权，重新打开表单才清理", () => {
  const { admin, context } = secretEditorHarness();
  admin.openCreate("feishu");
  vm.runInContext('channelRegistration.reset = () => { throw new Error("must retain registration"); }; channelRegistration.authorizedId = "retained-authorization";', context);
  vm.runInContext('setChannelRegistrationMode("manual"); setChannelRegistrationMode("scan");', context);
  assert.equal(vm.runInContext('channelRegistration.authorizedId', context), "retained-authorization");
  assert.throws(() => admin.openCreate("feishu"), /must retain registration/);
});

test("扫码过期及失败在右侧提供重试，初始生成按钮只用于空态", () => {
  const { context, getElement } = secretEditorHarness();
  for (const status of ["expired", "failed", "denied", "cancelled"]) {
    vm.runInContext(`renderChannelRegistration({status: "${status}"})`, context);
    assert.equal(getElement("channelRegistrationRetryScan").hidden, false);
    assert.equal(getElement("channelRegistrationStartScan").hidden, true);
    assert.equal(getElement("channelRegistrationQr").hidden, true);
  }
  vm.runInContext('renderChannelRegistration({status: "waiting"})', context);
  assert.equal(getElement("channelRegistrationRetryScan").hidden, false);
  vm.runInContext('renderChannelRegistration({status: "authorized"})', context);
  assert.equal(getElement("channelRegistrationRetryScan").hidden, true);
  vm.runInContext('renderChannelRegistration({status: "starting"})', context);
  assert.equal(getElement("channelRegistrationRetryScan").hidden, true);
  assert.equal(getElement("channelRegistrationStartScan").disabled, true);
  vm.runInContext('renderChannelRegistration({})', context);
  assert.equal(getElement("channelRegistrationStartScan").hidden, false);
  assert.equal(getElement("channelRegistrationStartScan").disabled, false);
});


test("飞书编辑保留已有域名及群聊策略", async () => {
  const { admin, getElement, requests, context } = secretEditorHarness();
  vm.runInContext('channelAdminState.accounts[0].channel_type = "feishu"; channelAdminState.accounts[0].settings = {feishu: {domain: "feishu", require_mention: true, group_allowlist: ["oc_allowed"]}};', context);
  admin.openEdit("channel-1");
  await admin.save({preventDefault() {}, currentTarget: getElement("channelAccountForm")});
  assert.equal(requests.at(-1).payload.settings.feishu.domain, "feishu");
  assert.equal(requests.at(-1).payload.settings.feishu.require_mention, true);
  assert.deepEqual(requests.at(-1).payload.settings.feishu.group_allowlist, ["oc_allowed"]);
});

test("新建选择 Agent 覆盖名称，编辑渠道不自动重命名", () => {
  const { admin, getElement } = secretEditorHarness();
  for (const type of ["feishu", "wecom"]) {
    admin.openCreate(type);
    assert.match(getElement("channelAgentId").innerHTML, /value="" disabled hidden selected>请选择/);
    getElement("channelName").value = "旧名称";
    getElement("channelAgentId").listeners.change({target: {value: "agent-1"}});
    assert.equal(getElement("channelName").value, "示例 Agent");
  }
  admin.openEdit("channel-1");
  getElement("channelAgentId").listeners.change({target: {value: "agent-1"}});
  assert.equal(getElement("channelName").value, "示例渠道");
});


test("扫码后改为手填保存会取消未消费的注册任务", async () => {
  const { admin, getElement, context } = secretEditorHarness();
  admin.openCreate("feishu");
  vm.runInContext('setChannelRegistrationMode("manual"); channelRegistration.reset = options => { window.resetOptions = options; };', context);
  await admin.save({preventDefault() {}, currentTarget: getElement("channelAccountForm")});
  assert.equal(context.window.resetOptions.consumed, false);
});


test("扫码授权成功明确提示待保存，不自动关闭或提交渠道", () => {
 const { admin, context, getElement, requests } = secretEditorHarness();
 admin.openCreate("feishu");
 vm.runInContext('renderChannelRegistration({status:"authorized"})', context);
 assert.equal(getElement("channelRegistrationAuthorized").hidden, false);
 assert.equal(getElement("channelRegistrationSaveReminder").hidden, false);
 assert.match(getElement("channelRegistrationScanStatus").textContent, /保存/);
 assert.equal(getElement("channelAccountModal").open, true);
 assert.equal(requests.length, 0);
 vm.runInContext('renderChannelRegistration({status:"expired"})', context);
 assert.equal(getElement("channelRegistrationAuthorized").hidden, true);
 assert.equal(getElement("channelRegistrationSaveReminder").hidden, true);
});

test("企微默认扫码且保留成员扩展，授权保存仅传企微句柄", async () => {
  const { admin, getElement, requests, context } = secretEditorHarness();
  admin.openCreate("wecom");
  assert.equal(getElement("channelRegistrationSetup").hidden, false);
  assert.equal(getElement("channelRegistrationScan").hidden, false);
  assert.equal(getElement("channelBotId").disabled, true);
  assert.equal(getElement("channelMemberTab").hidden, false);
  assert.equal(getElement("channelRegistrationManualLabel").textContent, "手动关联");
  assert.equal(getElement("channelRegistrationConsoleLink").href, "https://work.weixin.qq.com/");
  assert.doesNotMatch(getElement("channelRegistrationScanStatus").textContent, /预填/);
  vm.runInContext('channelRegistration.authorizedId = "wecom-ready";', context);
  getElement("channelBotId").value = "stale-bot";
  getElement("channelSecret").value = "stale-secret";
  await admin.save({ preventDefault() {}, currentTarget: getElement("channelAccountForm") });
  const payload = requests.at(-1).payload;
  assert.equal(payload.channel_type, "wecom");
  assert.equal(payload.registration_id, "wecom-ready");
  assert.equal(Object.hasOwn(payload, "secret"), false);
  assert.equal(Object.hasOwn(payload, "bot_id"), false);
  assert.equal(Object.hasOwn(payload.settings, "feishu"), false);
});

test("企微二维码拒绝非官方域名及带凭据的授权地址", () => {
  const { admin, getElement, context } = secretEditorHarness();
  context.URL = URL;
  context.qrcode = () => ({ addData() {}, make() {}, getModuleCount() { return 41; }, isDark() { return true; } });
  admin.openCreate("wecom");
  for (const url of ["https://evil.qq.com/", "https://work.weixin.qq.com.evil.test/", "http://work.weixin.qq.com/", "https://user:pass@work.weixin.qq.com/"]) {
    vm.runInContext(`renderChannelRegistration({status: "waiting", verification_url: ${JSON.stringify(url)}})`, context);
    assert.equal(getElement("channelRegistrationQr").hidden, true);
    assert.equal(getElement("channelRegistrationAuthLink").hidden, true);
    assert.equal(getElement("channelRegistrationRetryScan").hidden, false);
  }
  vm.runInContext('renderChannelRegistration({status:"waiting",verification_url:"https://work.weixin.qq.com/ai/qc/c?s=fixture"})', context);
  assert.equal(getElement("channelRegistrationQr").hidden, false);
  assert.equal(getElement("channelRegistrationAuthLink").hidden, true);
  assert.match(getElement("channelRegistrationQr").innerHTML, /wecom\.png/);
  admin.openCreate("feishu");
  vm.runInContext('renderChannelRegistration({status:"waiting",verification_url:"https://accounts.feishu.cn/fixture"})', context);
  assert.equal(getElement("channelRegistrationAuthLink").hidden, false);
  assert.match(getElement("channelRegistrationQr").innerHTML, /feishu\.ico/);
});

test("帮助切回支持消息和重新打开不会被迟到命令结果切页", async () => {
  let resolve;
  const { admin, elements } = commandHarness(() => new Promise(done => { resolve = done; }));
  admin.openHelp();
  const pending = admin.selectHelpTab("commands");
  admin.selectHelpTab("messages");
  resolve({ commands: [{ command: "/help", description: "帮助" }], notes: [] });
  await pending;
  assert.equal(elements.channelHelpMessagesPanel.hidden, false);
  assert.equal(elements.channelHelpCommandsPanel.hidden, true);
  admin.openHelp();
  assert.equal(elements.channelHelpMessagesPanel.hidden, false);
});


test("飞书资料开关回显并保存，保留其他渠道设置", async () => {
  const h = secretEditorHarness();
  vm.runInContext('channelAdminState.accounts[0].channel_type = "feishu"; channelAdminState.accounts[0].settings = {feishu:{user_details_enabled:true, require_mention:false}}', h.context);
  h.admin.openEdit("channel-1");
  assert.equal(h.getElement("channelUserDetailsEnabled").getAttribute("aria-checked"), "true");
  assert.equal(h.getElement("channelWecomApplicationField").hidden, true);
  vm.runInContext('setChannelUserDetailsEnabled(false)', h.context);
  await h.admin.save({preventDefault(){}, currentTarget:h.getElement("channelAccountForm")});
  assert.equal(h.requests.at(-1).payload.settings.feishu.user_details_enabled, false);
  assert.equal(h.requests.at(-1).payload.settings.feishu.require_mention, false);
});

test("权限检测只用已保存凭据，仅开启后允许检测并拒绝迟到结果", async () => {
  const h = secretEditorHarness();
  h.context.AbortController = AbortController; h.context.URL = URL;
  const requests = []; let resolve;
  h.context.apiFetch = (path, options) => { requests.push({path, options}); return new Promise(r => {resolve = r;}); };
  vm.runInContext('channelAdminState.accounts[0].channel_type = "feishu"', h.context);
  h.admin.openEdit("channel-1");
  const check = () => vm.runInContext('checkChannelUserDetails()', h.context);
  await check(); assert.equal(requests.length, 0);
  assert.equal(h.getElement("channelFeishuUserDetails").hidden, true);
  vm.runInContext("setChannelUserDetailsEnabled(true)", h.context);
  assert.equal(h.getElement("channelFeishuUserDetails").hidden, false);
  h.getElement("channelBotId").value = "new-id";
  await check(); assert.equal(requests.length, 0);
  assert.match(h.getElement("channelUserDetailsCheckResult").textContent, /先保存/);
  h.getElement("channelBotId").value = "bot-1";
  const pending = check();
  assert.equal(requests[0].path, "/api/v1/channel-accounts/channel-1/user-details/check");
  assert.equal(requests[0].options.method, "POST");
  vm.runInContext('clearChannelPermissionCheck()', h.context);
  assert.equal(requests[0].options.signal.aborted, true);
  resolve({status:"authorized", message:"old"}); await pending;
  assert.equal(h.getElement("channelUserDetailsCheckResult").hidden, true);
  const current = check();
  resolve({status:"authorized", message:"当前权限", permissions:[], console_url:"https://open.feishu.cn/app/demo/auth", docs_url:"javascript:alert(1)"});
  await current;
  assert.match(h.getElement("channelUserDetailsCheckResult").innerHTML, /当前权限/);
  assert.match(h.getElement("channelUserDetailsCheckResult").innerHTML, /前往权限管理/);
  assert.doesNotMatch(h.getElement("channelUserDetailsCheckResult").innerHTML, /javascript:/);
});


test("钉钉复用扫码创建，手填凭据不引入企微资料设置", async () => {
  const h = secretEditorHarness();
  h.admin.openCreate("dingtalk");
  assert.equal(h.getElement("channelBotIdLabel").textContent, "Client ID");
  assert.equal(h.getElement("channelSecretLabel").textContent, "Client Secret");
  assert.equal(h.getElement("channelMemberTab").hidden, false);
  vm.runInContext('channelRegistration.authorizedId = "ding-ready";', h.context);
  await h.admin.save({preventDefault(){}, currentTarget:h.getElement("channelAccountForm")});
  const payload = h.requests.at(-1).payload;
  assert.equal(payload.channel_type, "dingtalk");
  assert.equal(payload.registration_id, "ding-ready");
  assert.equal(Object.hasOwn(payload.settings, "wecom"), false);
  assert.equal(Object.hasOwn(payload.settings, "feishu"), false);
});


test("飞书编辑后新建钉钉清理凭据、授权及检测结果", () => {
  const h = secretEditorHarness();
  vm.runInContext('channelAdminState.accounts[0].channel_type = "feishu"; channelAdminState.accounts[0].settings = {feishu:{user_details_enabled:true}}', h.context);
  h.admin.openEdit("channel-1");
  h.getElement("channelSecret").value = "unsaved-credential";
  h.getElement("channelUserDetailsCheckResult").textContent = "已授权";
  let resets = 0;
  h.context.countRegistrationReset = () => { resets++; };
  vm.runInContext('channelRegistration.reset = countRegistrationReset; channelRegistration.authorizedId = "old-feishu";', h.context);
  h.admin.openCreate("dingtalk");
  assert.equal(resets, 1);
  assert.equal(h.getElement("channelBotId").value, "");
  assert.equal(h.getElement("channelSecret").value, "");
  assert.equal(h.getElement("channelSecret").dataset.maskedValue, "");
  assert.equal(h.getElement("channelSessionUserId").value, "");
  assert.equal(h.getElement("channelUserDetailsCheckResult").hidden, true);
  assert.equal(h.getElement("channelUserDetailsCheckResult").textContent, "");
  assert.equal(h.getElement("channelFeishuUserDetails").hidden, true);
  assert.equal(h.getElement("channelUserDetailsEnabled").getAttribute("aria-checked"), "false");
  assert.equal(vm.runInContext('channelRegistration.authorizedId', h.context), undefined);
});

test("钉钉仅保留应用与用户详情扩展，用户详情默认关闭并独立保存", async () => {
  const h = secretEditorHarness();
  h.admin.openCreate("dingtalk");
  assert.equal(h.getElement("channelDingtalkUserDetails").hidden, false);
  assert.doesNotMatch(templateHtml, /id="channelStreaming|id="channelCardTemplate|id="channelCardContent/);
  assert.equal(h.getElement("channelUserDetailsEnabled").getAttribute("aria-checked"), "false");
  vm.runInContext('channelAdminState.accounts[0].channel_type = "dingtalk"; channelAdminState.accounts[0].settings = {dingtalk:{user_details_enabled:true}}', h.context);
  h.admin.openEdit("channel-1");
  assert.equal(h.getElement("channelUserDetailsEnabled").getAttribute("aria-checked"), "true");
  await h.admin.save({preventDefault(){}, currentTarget:h.getElement("channelAccountForm")});
  assert.deepEqual(Object.keys(h.requests.at(-1).payload.settings.dingtalk), ["user_details_enabled"]);
  assert.equal(h.requests.at(-1).payload.settings.dingtalk.user_details_enabled, true);
  h.admin.openCreate("feishu");
  assert.equal(h.getElement("channelDingtalkUserDetails").hidden, true);
  assert.match(templateHtml, /不主动查询通讯录/);
});
