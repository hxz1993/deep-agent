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
  assert.match(workspaceSource, /压缩前\/后/);
  assert.match(workspaceSource, /Checkpoint/);
  assert.doesNotMatch(workspaceSource, /summary_body|checkpoint\.payload|摘要正文/i);
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
