import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { formatSessionIdSubtitle } from "../../../frontend/console/session-display.js";

const template = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);
const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const workspaceSource = await readFile(
  new URL("../../../frontend/console/workspace.js", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);
const consoleIndex = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);

test("会话标题第二行显示带前缀的完整会话 ID", () => {
  const sessionId = "019f1857-9463-7db3-a9d3-654a4ac24b24";
  assert.equal(formatSessionIdSubtitle(sessionId), `会话 ID：${sessionId}`);
  assert.equal(formatSessionIdSubtitle(null), "");
  assert.match(
    template,
    /id="chatTitle"[\s\S]*?class="session-badge session-subtitle" id="sessionBadge"/,
  );
  assert.match(
    chatSource,
    /sessionBadge\.textContent = formatSessionIdSubtitle\(state\.sessionId\)/,
  );
  assert.doesNotMatch(chatSource, /sessionBadge\.textContent = .*slice\(/);
});

test("工作台只读展示上下文预算、压缩结果与 Checkpoint 状态", () => {
  assert.match(chatSource, /\/context-status/);
  assert.match(chatSource, /last_compaction:\s*data\.context\.compaction/);
  assert.match(chatSource, /void loadContextStatus\(state\.sessionId\)/);
  assert.match(workspaceSource, /历史估算/);
  assert.match(workspaceSource, /上下文窗口/);
  assert.match(workspaceSource, /输出预估/);
  assert.match(workspaceSource, /安全余量/);
  assert.match(workspaceSource, /可用输入预算/);
  assert.match(workspaceSource, /压缩前\/后/);
  assert.match(workspaceSource, /Checkpoint/);
  assert.match(workspaceSource, /state\.lastRunUsage/);
  assert.match(chatSource, /state\.lastRunUsage = \{ \.\.\.usage \}/);
  assert.doesNotMatch(workspaceSource, /summary_body|checkpoint\.payload|摘要正文/i);
});

test("会话详情合并会话和上下文且不再重复展示工具与事件", () => {
  assert.match(template, /data-open-context[\s\S]*测试Header/);
  assert.match(template, /id="toggleInspectorPaneBtn"[\s\S]*会话详情/);
  assert.match(workspaceSource, /function toggleInspectorPane\(\)/);
  assert.match(workspaceSource, /<strong>当前会话<\/strong>/);
  assert.match(workspaceSource, /inspector-session-group[\s\S]*<strong>请求Header<\/strong>/);
  assert.match(workspaceSource, /<dt>User ID<\/dt>[\s\S]*identity\.user/);
  assert.match(workspaceSource, /<strong>上下文预算<\/strong>/);
  assert.match(workspaceSource, /inspector-budget-section[\s\S]*budget-meter[\s\S]*<strong>上下文预算<\/strong>/);
  assert.doesNotMatch(consoleIndex, /data-inspector-tab|>工具<|>事件</);
  assert.doesNotMatch(workspaceSource, /activeInspectorTab|eventRows|eventToolRows/);
  assert.doesNotMatch(chatSource, /deep-agent-console-event/);
});

test("Agent 默认使用下拉切换并可固定为左侧列表", () => {
  assert.match(workspaceSource, /agentSwitcher\.className = "agent-switcher"/);
  assert.match(workspaceSource, /class="agent-select-field"/);
  assert.match(workspaceSource, /id="workspaceAgentSelect"/);
  assert.match(workspaceSource, /id="pinAgentPaneBtn"/);
  assert.match(consoleIndex, /class="pane-heading-actions"/);
  assert.match(consoleIndex, /id="unpinAgentPaneBtn"/);
  assert.doesNotMatch(consoleIndex, /class="agent-context-action"/);
  assert.match(workspaceSource, /setAgentPanePinned\(false\)/);
  assert.match(workspaceSource, /grid\.classList\.toggle\("agent-pinned", pinned\)/);
  assert.match(workspaceSource, /api\.selectAgent\(agentId\)/);
  assert.match(workspaceSource, /sidebarHeader\?\.remove\(\)/);
  assert.doesNotMatch(workspaceSource, /sessionPanelSummary\.className/);
  assert.match(consoleCss, /\.workspace-grid > \.agent-pane \{ display: none; \}/);
  assert.match(consoleCss, /\.workspace-grid\.agent-pinned > \.agent-pane \{ display: flex; \}/);
  assert.match(consoleCss, /\.workspace-grid\.agent-pinned #workspaceLegacyHost \.agent-switcher \{ display: none; \}/);
  assert.match(consoleCss, /\.pane-heading > \.pane-heading-actions[\s\S]*flex-direction: row/);
  assert.match(consoleCss, /\.agent-select-field[\s\S]*background: var\(--bg\)/);
  assert.match(consoleCss, /@media \(max-width: 920px\)[\s\S]*#pinAgentPaneBtn \{ display: none; \}/);
});

test("主题选中态在主题容器内保持有间距的圆角正方形", () => {
  assert.match(consoleCss, /\.theme-switch \{[^}]*height: 30px;[^}]*padding: 3px/);
  assert.match(consoleCss, /\.theme-switch button \{[^}]*width: 22px;[^}]*height: 22px;[^}]*padding: 0;[^}]*border-radius: 4px/);
});

test("输入区按模型与模态输入双列展示并移除冗余提示", () => {
  assert.match(consoleCss, /#workspaceLegacyHost \.composer-meta[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(consoleCss, /#composerUploadCapabilities \{ grid-column: 1 \/ -1; \}/);
  assert.match(chatSource, /composer-modalities-label">模态输入/);
  assert.doesNotMatch(template, /deep-agent 可能会出错|composer-hint/);
});

test("0803 不带入 0802 的对话消息与滚动重构", () => {
  assert.doesNotMatch(chatSource, /message-expand-btn|scrollBottomBtn|appendAssistantCopyAction/);
  assert.doesNotMatch(chatSource, /tool-block open running/);
});

test("工作台压缩状态延迟显示并在正式输出或结束时清理", () => {
  assert.match(chatSource, /case "context_compaction":\s*assistant\.showContextCompaction/);
  assert.match(chatSource, /setTimeout\(\(\) => \{[\s\S]*\}, 150\)/);
  for (const eventType of ["thinking", "text", "tool_call", "final", "error", "cancelled"]) {
    assert.match(
      chatSource,
      new RegExp(`case "${eventType}":[\\s\\S]{0,100}hideContextCompaction`),
    );
  }
  assert.match(chatSource, /finally \{[\s\S]{0,100}assistant\.hideContextCompaction\(\)/);
});

test("搜索框与普通新建之间提供独立的测试会话入口", () => {
  assert.match(workspaceSource, /testChatButton\.id = "contextTestChatBtn"/);
  assert.match(workspaceSource, /data-lucide="flask-conical"/);
  assert.match(
    workspaceSource,
    /toolbar\.append\(search, testChatButton, newChatButton\)/,
  );
  assert.match(
    consoleCss,
    /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+31px\s+31px/,
  );
});

test("测试会话弹窗选择场景后创建、打开并预填推荐问题", () => {
  assert.match(workspaceSource, /dialog\.id = "contextTestDialog"/);
  assert.match(workspaceSource, /name="contextTestScenario"/);
  assert.match(workspaceSource, /api\/v1\/context-test-scenarios\/\$\{encodeURIComponent\(selected\.value\)\}/);
  assert.match(workspaceSource, /await api\.openSession\(/);
  assert.match(workspaceSource, /api\.setComposerText\(payload\.recommended_prompt\)/);
  assert.match(chatSource, /async openSession\(session\)/);
  assert.match(chatSource, /setComposerText\(text\)/);
});
