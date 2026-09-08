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

test("渠道表单绑定Agent且Secret不回显并支持留空保留", () => {
  assert.match(templateHtml, /id="channelAgentId"/);
  assert.match(templateHtml, /<span>会话用户<\/span>/);
  assert.match(templateHtml, /type="text" id="channelSessionUserId" maxlength="128" required/);
  assert.match(templateHtml, /id="channelSessionUserId"[^>]*aria-describedby="channelSessionUserHint"/);
  assert.match(templateHtml, /<small class="field-hint" id="channelSessionUserHint">使用此平台账号记录渠道会话。<\/small>/);
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
  assert.match(source, /secret\.placeholder = account\.secret_set \? "已配置，留空则保持不变"/);
  assert.match(source, /channelAccountModalTitle"\)\.textContent = account\.name/);
  assert.doesNotMatch(source, /编辑企微渠道 ·/);
  assert.doesNotMatch(source, /account\.secret\b/);
  assert.match(source, /if \(secret\) payload\.secret = secret/);
  assert.match(source, /\/api\/v1\/channel-accounts/);
  assert.match(source, /\/test-connection/);
});

test("渠道启停只在列表使用Switch切换", () => {
  assert.match(source, /data-toggle-channel=/);
  assert.match(source, /role="switch"/);
  assert.match(source, /channel-row-actions compact-icon-actions[\s\S]*data-toggle-channel=[\s\S]*data-channel-test=[\s\S]*data-channel-delete=/);
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
  assert.match(styles, /\.channel-row-actions \.model-enabled-switch\.on\s*\{\s*background: var\(--accent\);/);
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
