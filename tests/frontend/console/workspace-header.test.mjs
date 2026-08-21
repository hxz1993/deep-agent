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
  assert.match(workspaceSource, /<strong>当前 Run<\/strong>/);
  assert.match(workspaceSource, /Conversation ID/);
  assert.match(workspaceSource, /Run ID/);
  assert.match(workspaceSource, /缓存读取/);
  assert.match(workspaceSource, /缓存写入/);
  assert.match(workspaceSource, /Tool Search/);
  assert.match(workspaceSource, /详细指标请查看运行日志/);
  assert.match(workspaceSource, /value === null \|\| value === undefined/);
  assert.match(workspaceSource, /state\.lastRunMetrics/);
  assert.match(chatSource, /conversation_id/);
  assert.match(chatSource, /run_metrics/);
  assert.match(workspaceSource, /inspector-budget-section[\s\S]*<strong>上下文预算<\/strong>[\s\S]*context-budget-chart/);
  assert.match(workspaceSource, /contextBudgetPercent\(value, contextWindow\)/);
  assert.match(workspaceSource, /role="progressbar"/);
  assert.doesNotMatch(consoleIndex, /data-inspector-tab|>工具<|>事件</);
  assert.doesNotMatch(workspaceSource, /activeInspectorTab|eventRows|eventToolRows/);
  assert.doesNotMatch(chatSource, /deep-agent-console-event/);
});

test("会话详情改为连续轻量甬道但保留全部既有属性", () => {
  for (const label of [
    "会话 ID", "Agent ID", "原始消息", "待发附件",
    "App", "Space", "Language", "User ID",
    "Conversation ID", "Run ID", "请求次数", "输入 Token", "输出 Token",
    "合计 Token", "缓存读取", "缓存写入", "工具调用", "Tool Search", "耗时", "压缩",
    "本轮压缩前/后", "Checkpoint Token", "估算范围", "估算器",
  ]) {
    assert.match(workspaceSource, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(consoleCss, /会话详情改为单一连续信息甬道/);
  assert.match(consoleCss, /\.inspector-section\s*\{[\s\S]*border: 0;[\s\S]*border-bottom:/);
  assert.match(consoleCss, /\.inspector-section\s*\{[\s\S]*box-shadow: none/);
});

test("桌面工作台左右边栏支持有范围的拖拽和键盘调宽", () => {
  assert.match(consoleIndex, /id="agentPaneResizer"[\s\S]*data-resize-pane="agent"/);
  assert.match(consoleIndex, /id="inspectorPaneResizer"[\s\S]*data-resize-pane="inspector"/);
  assert.match(workspaceSource, /id = "sessionPaneResizer"/);
  assert.match(workspaceSource, /session: \{ min: 220, max: 420, initial: 300/);
  assert.match(workspaceSource, /inspector: \{ min: 260, max: 460, initial: 320/);
  assert.match(workspaceSource, /clampWorkspacePaneWidth/);
  assert.match(workspaceSource, /setPointerCapture/);
  assert.match(workspaceSource, /\["ArrowLeft", "ArrowRight", "Home"\]/);
  assert.match(workspaceSource, /bindWorkspaceResizers\(\)/);
  assert.match(consoleCss, /\.workspace-resizer[\s\S]*cursor: col-resize/);
  assert.match(consoleCss, /@media \(max-width: 1260px\)[\s\S]*\.workspace-resizer-inspector \{ display: none; \}/);
  assert.match(consoleCss, /@media \(max-width: 620px\)[\s\S]*\.workspace-resizer \{ display: none !important; \}/);
});

test("Agent 默认使用无边框图标下拉并支持刷新与固定列表", () => {
  assert.match(workspaceSource, /agentSwitcher\.className = "agent-switcher"/);
  assert.match(workspaceSource, /className = "agent-select-field"/);
  assert.match(workspaceSource, /agentSelect\.setAttribute\("aria-label", "切换测试 Agent"\)/);
  assert.doesNotMatch(workspaceSource, /previousAgentBtn|nextAgentBtn|cycleWorkspaceAgent/);
  assert.match(workspaceSource, /id="refreshWorkspaceAgentsBtn"/);
  assert.doesNotMatch(workspaceSource, /id="workspaceAgentSelect"/);
  assert.match(workspaceSource, /id="pinAgentPaneBtn"/);
  assert.match(consoleIndex, /class="pane-heading-actions"/);
  assert.match(consoleIndex, /id="unpinAgentPaneBtn"/);
  assert.doesNotMatch(consoleIndex, /class="agent-context-action"/);
  assert.match(workspaceSource, /setAgentPanePinned\(false\)/);
  assert.match(workspaceSource, /grid\.classList\.toggle\("agent-pinned", pinned\)/);
  assert.match(workspaceSource, /api\.selectAgent\(agentId\)/);
  assert.match(chatSource, /el\.agentSelect\.disabled = state\.streaming \|\| !!state\.sessionId/);
  assert.match(workspaceSource, /function refreshWorkspaceAgents\(button\)/);
  assert.match(workspaceSource, /sidebarHeader\?\.remove\(\)/);
  assert.doesNotMatch(workspaceSource, /sessionPanelSummary\.className/);
  assert.match(consoleCss, /\.workspace-grid > \.agent-pane \{ display: none; \}/);
  assert.match(consoleCss, /\.workspace-grid\.agent-pinned > \.agent-pane \{ display: flex; \}/);
  assert.match(consoleCss, /\.workspace-grid\.agent-pinned #workspaceLegacyHost \.agent-switcher \{ display: none; \}/);
  assert.match(consoleCss, /\.pane-heading > \.pane-heading-actions[\s\S]*flex-direction: row/);
  assert.match(consoleCss, /\.agent-select-field[\s\S]*border: 0;[\s\S]*background: transparent/);
  assert.match(consoleCss, /\.agent-select-field select[\s\S]*border: 0;[\s\S]*appearance: none/);
  assert.match(consoleCss, /\.agent-switcher \.icon-action[\s\S]*border: 0;/);
  assert.match(consoleCss, /@media \(max-width: 920px\)[\s\S]*#pinAgentPaneBtn \{ display: none; \}/);
});

test("聊天标题栏横跨右侧详情并在详情收起时恢复独立卡片", () => {
  assert.match(consoleCss, /\.chat-header[\s\S]*width: calc\(100% \+ var\(--console-resizer-width\) \+ var\(--console-inspector-width\)\)/);
  assert.match(consoleCss, /\.inspector-pane[\s\S]*padding-top: 56px;[\s\S]*border-left: 0 !important/);
  assert.match(consoleCss, /\.workspace-grid\.inspector-hidden #workspaceLegacyHost \.chat-header[\s\S]*width: calc\(100% \+ 1px\)/);
});

test("主题选中态在主题容器内保持有间距的圆角正方形", () => {
  assert.match(consoleCss, /\.theme-switch \{[^}]*height: 30px;[^}]*padding: 3px;[^}]*border-radius: 9px/);
  assert.match(consoleCss, /\.theme-switch button \{[^}]*width: 22px;[^}]*height: 22px;[^}]*padding: 0;[^}]*border-radius: 7px/);
});

test("输入区按模型与模态输入双列展示并移除冗余提示", () => {
  assert.match(consoleCss, /#workspaceLegacyHost \.composer-meta[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(consoleCss, /#composerUploadCapabilities \{ grid-column: 1 \/ -1; \}/);
  assert.match(chatSource, /composer-modalities-label">模态输入/);
  assert.doesNotMatch(template, /deep-agent 可能会出错|composer-hint/);
});

test("空会话不再展示预设开场白", () => {
  assert.doesNotMatch(template, /id="suggestions"|class="suggestion"/);
  assert.doesNotMatch(template, /现在几点了|今天的待办|快速排序/);
  assert.doesNotMatch(chatSource, /querySelectorAll\("\.suggestion"\)/);
});

test("输入框提供线性附件、主动压缩按钮和后端预算百分比", () => {
  assert.match(template, /id="attachBtn"[\s\S]*?data-lucide="paperclip"/);
  assert.match(template, /id="compactContextBtn"[\s\S]*?data-lucide="minimize-2"/);
  assert.match(template, /id="contextBudgetBadge"[\s\S]*上下文 —/);
  assert.doesNotMatch(template, /📎/);
  assert.doesNotMatch(chatSource, /📎/);
  assert.match(chatSource, /estimated_history_tokens/);
  assert.match(chatSource, /usable_request_tokens/);
  assert.match(chatSource, /manual_compaction_available/);
  assert.match(chatSource, /\/compact/);
  assert.match(chatSource, /context_compaction_result/);
  assert.doesNotMatch(chatSource, /result\.context_status/);
  assert.match(chatSource, /status === "noop"[\s\S]*?await loadContextStatus\(session\)/);
  assert.match(chatSource, /本次压缩未产生明显效果/);
  assert.match(chatSource, /可再次压缩，重复压缩可能损失部分细节/);
  assert.doesNotMatch(chatSource, /already_compacted|insufficient_new_history/);
  assert.doesNotMatch(chatSource, /context_compaction_target_ratio|safety_margin\s*=|predict.*output/i);
});

test("停止生成只请求正式取消接口并等待真实 cancelled 事件", () => {
  assert.match(chatSource, /sessionApiUrl\(\{ id: sessionId, agentId \}, "\/cancel"\)/);
  assert.match(chatSource, /body\.status === "cancel_requested"/);
  assert.match(chatSource, /case "cancelled":[\s\S]*assistant\.markInterrupted\(\)/);
  assert.match(
    chatSource,
    /case "cancelled":[\s\S]*state\.skipHistoryRefresh = true;[\s\S]*break;/,
  );
  assert.match(chatSource, /case "cancelled":[\s\S]*renderUsage\(data\.usage\)/);
  assert.match(chatSource, /markInterrupted\(\)[\s\S]*for \(const block of toolBlocks\.values\(\)\)/);
  assert.match(chatSource, /tool-status-interrupted/);
  assert.match(chatSource, /state\.stopRequested/);
  assert.doesNotMatch(chatSource, /cancelFallbackTimer|abortFallback|setTimeout\(abortFallback/);
  assert.doesNotMatch(chatSource, /streamCancelled[\s\S]{0,120}setTimeout/);
  assert.doesNotMatch(chatSource, /addErrorTo\(assistant, "（已停止生成）"\)/);
});

test("业务重试或取消撤回工具调用时删除实时工具卡", () => {
  assert.match(
    chatSource,
    /case "tool_call_removed":[\s\S]*assistant\.removeToolCall\(data\)/,
  );
  assert.match(
    chatSource,
    /removeToolCall\(data\)[\s\S]*toolBlocks\.get\(key\)[\s\S]*block\.remove\(\)[\s\S]*toolBlocks\.delete\(key\)/,
  );
});

test("历史中的 interrupted 助手消息恢复已停止标记", () => {
  assert.match(chatSource, /presentation\.interrupted/);
  assert.match(chatSource, /message-run-status interrupted/);
  assert.match(chatSource, /circle-stop/);
});

test("当前有效 Checkpoint 作为历史分隔标记而非聊天消息渲染", () => {
  assert.match(chatSource, /data\.context_checkpoint/);
  assert.match(chatSource, /addContextCheckpointMarker/);
  assert.match(chatSource, /上下文已压缩 · 原始约/);
  assert.match(chatSource, /buildHistoryVisualItems\(messages, contextCheckpoint\)/);
  assert.doesNotMatch(chatSource, /addMessage\([^\n]*context_checkpoint/);
});

test("0803 不带入 0802 的对话消息与滚动重构", () => {
  assert.doesNotMatch(chatSource, /message-expand-btn|scrollBottomBtn|appendAssistantCopyAction/);
  assert.doesNotMatch(chatSource, /tool-block open running/);
});

test("自动和主动压缩复用独立过程条并在成功后刷新历史标记", () => {
  assert.match(chatSource, /function createContextCompactionRow\(beforeNode = null\)/);
  assert.match(chatSource, /function setContextCompactionRow\(/);
  assert.match(chatSource, /case "context_compaction":\s*showAutomaticContextCompaction\(data, assistant\.row\)/);
  assert.match(chatSource, /automaticCompactionTimer = window\.setTimeout\([\s\S]*?, 150\)/);
  assert.match(chatSource, /finalizeAutomaticContextCompaction\(data\.context, data\.status\)/);
  assert.match(chatSource, /state\.skipHistoryRefresh = !refreshCompactionHistory/);
  assert.match(chatSource, /本轮临时缩减，未更新 Checkpoint/);
  assert.doesNotMatch(chatSource, /assistant\.showContextCompaction|assistant\.hideContextCompaction/);
  assert.doesNotMatch(chatSource, /className = "context-compaction-status"/);
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
