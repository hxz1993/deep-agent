import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const source = await readFile(
  new URL("../../../frontend/console/configuration/channels.js", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../frontend/console/configuration/configuration.css", import.meta.url),
  "utf8",
);

test("配置中心提供企微渠道页和完整运行状态", () => {
  assert.match(indexHtml, /data-tab="channels"[\s\S]*?<span>渠道<\/span>/);
  assert.match(templateHtml, /id="panel-channels"/);
  assert.match(templateHtml, /id="channelRuntimeBanner"/);
  assert.match(templateHtml, /panel-toolbar-actions[\s\S]*id="channelRuntimeBanner"[\s\S]*id="refreshChannelsBtn"/);
  assert.match(templateHtml, /企微网关：读取中/);
  assert.match(templateHtml, /id="refreshChannelsBtn" title="刷新企微网关状态"[\s\S]*网关状态<\/button>/);
  assert.doesNotMatch(templateHtml, />刷新状态<\/button>/);
  assert.doesNotMatch(templateHtml, /channel-runtime-banner/);
  assert.match(source, /not_deployed:\s*"未部署"/);
  assert.match(source, /unreachable:\s*"运行时不可达"/);
  assert.match(source, /connected:\s*"已连接"/);
  assert.match(source, /conflict:\s*"连接冲突"/);
  assert.match(source, /textContent = `企微网关：\$\{stateLabel\}`/);
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
