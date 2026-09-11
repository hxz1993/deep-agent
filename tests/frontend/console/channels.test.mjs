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

test("配置中心提供企微渠道页和完整运行状态", () => {
  assert.match(indexHtml, /data-tab="channels"[\s\S]*?<span>渠道<\/span>/);
  assert.match(templateHtml, /id="panel-channels"/);
  assert.match(templateHtml, /id="channelRuntimeBanner"/);
  assert.match(templateHtml, /panel-toolbar-actions[\s\S]*id="channelRuntimeBanner"[\s\S]*id="refreshChannelsBtn"/);
  assert.match(templateHtml, /连接外部聊天平台，将消息交给绑定的 Agent 处理。/);
  assert.match(templateHtml, /网关：读取中/);
  assert.match(templateHtml, /id="refreshChannelsBtn" title="刷新渠道网关状态"[\s\S]*网关状态<\/button>/);
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
    channelCommandsModal: { open: false, showModal() { this.open = true; } },
    channelCommandsContent: { innerHTML: "" },
    retryChannelCommandsBtn: { hidden: false },
  };
  const requests = [];
  const context = vm.createContext({
    window: {},
    document: { addEventListener() {}, getElementById(id) { return elements[id]; } },
    apiFetch(path) { requests.push(path); return fetchCatalog(); },
    escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); },
  });
  vm.runInContext(source, context);
  return { admin: context.window.channelsAdmin, elements, requests, context };
}

test("快捷命令在渠道标题后以带图标的无框按钮展示", () => {
  const panel = templateHtml.split('id="panel-channels">')[1].split('<div class="table-wrap')[0];
  assert.match(panel, /channel-title-row[\s\S]*<h2>渠道<\/h2>\s*<button class="channel-commands-btn" type="button" id="channelCommandsBtn">[\s\S]*?data-lucide="terminal"[\s\S]*?<span>快捷命令<\/span><\/button>/);
  assert.doesNotMatch(panel.split('class="panel-toolbar-actions"')[1], /channelCommandsBtn/);
  assert.match(styles, /\.channel-commands-btn\s*\{[^}]*border: 0;[^}]*background: transparent;/);
});

test("渠道标题沿用目录标题基线，快捷命令热区不撑高标题行", () => {
  assert.match(consoleStyles, /#configurationHost #panel-channels \.panel-title-copy\s*\{\s*display: block;/);
  assert.match(consoleStyles, /#configurationHost \.channel-title-row \.channel-commands-btn\s*\{\s*margin-block: -4px;/);
  assert.match(styles, /\.channel-title-row \.channel-commands-btn\s*\{[^}]*min-height: 28px;/);
});

test("快捷命令弹框独立于网关读取统一只读目录并转义内容", async () => {
  const { admin, elements, requests } = commandHarness(async () => ({
    commands: [{ command: "/help", description: "说明 <script>" }],
    notes: ["群聊影响 <所有成员>"],
  }));
  await admin.openCommands();
  assert.deepEqual(requests, ["/api/v1/channel-commands"]);
  assert.equal(elements.channelCommandsModal.open, true);
  assert.match(elements.channelCommandsContent.innerHTML, /<code>\/help<\/code>/);
  assert.match(elements.channelCommandsContent.innerHTML, /说明 &lt;script&gt;/);
  assert.doesNotMatch(elements.channelCommandsContent.innerHTML, /<script>|<button|执行/);
  assert.equal(elements.retryChannelCommandsBtn.hidden, true);
  assert.match(templateHtml, /id="channelCommandsBtn"[\s\S]*快捷命令/);
  assert.match(templateHtml, /id="channelCommandsModal" aria-labelledby="channelCommandsModalTitle"/);
  assert.doesNotMatch(source, /command: "\/(help|new|stop|status|whoami)"/);
});

test("快捷命令请求失败就近展示错误并支持重试", async () => {
  let attempt = 0;
  const { admin, elements } = commandHarness(async () => {
    if (!attempt++) throw new Error("HTTP 503：暂不可用");
    return { commands: [{ command: "/help", description: "帮助" }], notes: [] };
  });
  await admin.openCommands();
  assert.match(elements.channelCommandsContent.innerHTML, /role="alert".*HTTP 503：暂不可用/);
  assert.equal(elements.retryChannelCommandsBtn.hidden, false);
  await admin.openCommands();
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
  assert.match(templateHtml, /<span>会话用户<\/span>/);
  assert.match(templateHtml, /type="text" id="channelSessionUserId" maxlength="128" required/);
  assert.match(templateHtml, /id="channelSessionUserId"[^>]*aria-describedby="channelSessionUserHint"/);
  assert.match(templateHtml, /field-label-row[^\n]*<span>会话用户<\/span><small class="field-subtitle" id="channelSessionUserHint">使用此平台账号记录渠道会话。<\/small>/);
  assert.match(source, /session_user_id: document.getElementById\("channelSessionUserId"\).value.trim\(\)/);
  assert.match(source, /channelSessionUserId"\).value = account.session_user_id/);
  assert.match(templateHtml, /id="channelBotId"/);
  assert.match(templateHtml, /渠道名称/);
  assert.doesNotMatch(templateHtml, /渠道类型/);
  assert.match(templateHtml, /id="channelBotId"[\s\S]*<span>Secret<\/span>[\s\S]*id="channelSecret"/);
  assert.doesNotMatch(templateHtml, /channel-form-wide/);
  assert.match(templateHtml, /<input type="password" id="channelSecret"/);
  assert.doesNotMatch(templateHtml, /0\.7\.0 按普通字符串保存/);
  assert.doesNotMatch(templateHtml, /id="channelWelcomeMessage"|id="channelEnabled"/);
  assert.doesNotMatch(templateHtml, /owner_user_id|platform_token|cookie/i);
  assert.doesNotMatch(templateHtml, /channelAppId|channelSpaceId|channelLanguage|欢迎语/);
  assert.doesNotMatch(source, /app_id|space_id|language|welcome/i);
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
      value: "", dataset: {}, listeners: {}, open: false, selected: false,
      addEventListener(type, listener) { this.listeners[type] = listener; },
      showModal() { this.open = true; }, close() { this.open = false; },
      select() { this.selected = true; }, reportValidity() { return true; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    window: {},
    document: {
      addEventListener(type, listener) { if (type === "DOMContentLoaded") ready.push(listener); },
      getElementById: getElement,
    },
    apiFetch: async (path, options) => { requests.push({ path, ...options, payload: JSON.parse(options.body) }); },
    AdminFormFeedback: { clear() {}, show() { assert.fail("unexpected save failure"); } },
    escapeHtml: (value) => String(value ?? ""),
    showToast() {},
  });
  vm.runInContext(source, context);
  vm.runInContext(`channelAdminState.accounts = [
    { id: "channel-1", name: "示例渠道", agent_id: "agent-1", bot_id: "bot-1", session_user_id: "user-1", secret_set: true, secret_masked: "sample********1234" },
    { id: "channel-2", name: "另一渠道", agent_id: "agent-1", bot_id: "bot-2", session_user_id: "user-2", secret_set: true, secret_masked: "other-********5678" }
  ]; channelAdminState.agents = [{ id: "agent-1", name: "示例 Agent" }];`, context);
  ready.forEach((listener) => listener());
  const admin = context.window.channelsAdmin;
  admin.refresh = async () => {};
  return { admin, getElement, requests };
}

test("渠道Secret回显安全掩码，聚焦全选且编辑后隐藏新值", () => {
  const { admin, getElement } = secretEditorHarness();
  admin.openEdit("channel-1");
  const secret = getElement("channelSecret");
  assert.equal(secret.value, "sample********1234");
  assert.equal(secret.type, "text");
  assert.equal(secret.required, false);
  secret.listeners.focus({ currentTarget: secret });
  assert.equal(secret.selected, true);
  secret.value = "replacement-fixture";
  secret.listeners.input({ currentTarget: secret });
  assert.equal(secret.type, "password");
  admin.openEdit("channel-2");
  assert.equal(secret.value, "other-********5678");
  assert.equal(secret.type, "text");
  admin.openCreate();
  assert.equal(secret.value, "");
  assert.equal(secret.dataset.maskedValue, "");
  assert.equal(secret.type, "password");
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
  assert.match(source, /企微（WS）/);
  assert.doesNotMatch(source, /channel-account-code|channel-transport/);
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

test("快捷命令弹框随内容收缩，长说明仅正文滚动", () => {
  assert.match(styles, /dialog\.console-detail-dialog\.modal\.channel-commands-modal\s*\{[^}]*height: fit-content;[^}]*min-height: 0;[^}]*max-height: calc\(100vh - 32px\);/);
  assert.match(styles, /#channelCommandsModal > \.modal-body\s*\{[^}]*max-height: calc\(100vh - 160px\);[^}]*overflow-y: auto;/);
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
  assert.match(templateHtml, /id="createWecomChannelBtn" role="menuitem"[^\n]*<span>企微<\/span>/);
});
