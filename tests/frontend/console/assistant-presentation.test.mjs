import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  isPlanningToolEvent,
  normalizePlanSnapshot,
  planPanelState,
  processDisclosureLabel,
} from "../../../frontend/console/assistant-presentation.js";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);
const workspaceTemplate = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);
const themesCss = await readFile(
  new URL("../../../frontend/console/themes.css", import.meta.url),
  "utf8",
);

test("过程、工具和 HITL 默认折叠且等待操作标题清晰", () => {
  assert.equal(processDisclosureLabel(), "处理过程");
  assert.equal(processDisclosureLabel({ running: true }), "处理过程 · 处理中");
  assert.equal(processDisclosureLabel({ pending: true }), "处理过程 · 等待操作");
  assert.match(chatSource, /processBlock\.className = "assistant-process hidden"/);
  assert.match(chatSource, /isSkillLoad = merged\.tool === "load_capability"/);
  assert.match(chatSource, /isSkillLoad \? "正在加载 Skill"/);
  assert.match(chatSource, /block\.classList\.add\("hitl-pending"\)/);
  assert.doesNotMatch(chatSource, /block\.classList\.add\("open", "hitl-pending"\)/);
  assert.match(chatSource, /textContent = `等待操作 · \$\{defaultTitle\}`/);
});

test("思考过程是独立的二级折叠，默认关闭且正文不受影响", () => {
  assert.match(
    chatSource,
    /<button type="button" class="assistant-thinking-head" aria-expanded="false">/,
  );
  assert.match(
    chatSource,
    /const block = thinkingBlock;[\s\S]*block\.classList\.toggle\("open", open\)/,
  );
  assert.match(
    chatSource,
    /thinkingHead\.setAttribute\("aria-expanded", String\(open\)\)/,
  );
  assert.match(workspaceCss, /\.assistant-thinking-body\s*\{[\s\S]*?display:\s*none;/);
  assert.match(
    workspaceCss,
    /\.assistant-thinking\.open \.assistant-thinking-body\s*\{\s*display:\s*block;/,
  );
  assert.doesNotMatch(chatSource, /thinkingBlock\.classList\.add\("open"\)/);
  assert.match(chatSource, /finalContent\.className = "assistant-final markdown streaming"/);
});

test("方案 A 使用线性文档层级并移除对话中的装饰性色块", () => {
  assert.match(workspaceCss, /\.msg-row\.user > \.avatar \{ display: none; \}/);
  assert.match(
    workspaceCss,
    /\.msg-row\.user \.bubble \{[\s\S]*?color: var\(--text\);[\s\S]*?border: 1px solid var\(--border-soft\);[\s\S]*?border-radius: 12px 12px 4px 12px;/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-process \{[\s\S]*?margin: 1px 0 16px;[\s\S]*?border-bottom: 1px solid var\(--border-soft\);/,
  );
  assert.match(workspaceCss, /\.assistant-process-icon \{ display: none; \}/);
  assert.match(workspaceCss, /\.tool-search-block \{[\s\S]*?border-style: solid;/);
  assert.match(
    workspaceCss,
    /\.tool-status-complete \{[^}]*color: var\(--text-faint\);/,
  );
  assert.match(
    workspaceCss,
    /\.context-ref-chip \{[\s\S]*?border: 1px solid var\(--border\);[\s\S]*?background: var\(--bg-soft\);/,
  );
  assert.doesNotMatch(
    themesCss,
    /--user-bubble: #(1f6feb|0969da|0b6e99|0047ab);/,
  );
  assert.doesNotMatch(
    themesCss,
    /--(tool|thinking)-bg: #(16241f|f0fff4|e8f5e9|f0fff0|1a1829|f3f0ff|ede9fe|f0f0ff);/,
  );
});

test("实时 deferred 续跑复用同一可视回答且 removed 即时删除工具卡", () => {
  assert.match(
    chatSource,
    /const assistant = state\.assistantPresenter\?\.row\?\.isConnected[\s\S]*\? state\.assistantPresenter[\s\S]*: createAssistantMessage\(\)/,
  );
  assert.match(
    chatSource,
    /removeToolCall\(data\)[\s\S]*block\.remove\(\)[\s\S]*toolBlocks\.delete\(key\)/,
  );
  assert.match(chatSource, /case "final":[\s\S]*assistant\.complete\(data\.output\)/);
  assert.match(chatSource, /case "cancelled":[\s\S]*assistant\.markInterrupted\(\)/);
  assert.match(chatSource, /case "error":[\s\S]*addErrorTo\(assistant/);
});

test("大工具结果显示外置提示并保留预览", () => {
  assert.match(chatSource, /renderToolResultArtifacts\(data\.artifacts\)/);
  assert.match(chatSource, /完整结果已外置/);
  assert.match(chatSource, /Agent 可按需读取/);
  assert.match(workspaceCss, /\.tool-result-artifact/);
});

test("任务计划在消息区底部浮层展示最新快照", () => {
  assert.equal(isPlanningToolEvent({ source: "planning", tool: "anything" }), false);
  assert.equal(isPlanningToolEvent({ tool: "write_plan" }), true);
  assert.equal(isPlanningToolEvent({ source: "planning", tool: "update_task_statuses" }), false);
  assert.equal(isPlanningToolEvent({ tool: "execute_python" }), false);

  const running = normalizePlanSnapshot({
    items: [
      { id: "1", content: "读取输入", active_form: "正在读取输入", status: "in_progress" },
      { id: "2", content: "生成结果", status: "pending" },
    ],
  });
  assert.deepEqual(planPanelState(running), {
    plan: running,
    label: "0/2",
  });

  const completed = normalizePlanSnapshot({
    items: [
      { id: "1", content: "读取输入", status: "completed" },
      { id: "2", content: "生成结果", status: "completed" },
    ],
  });
  assert.deepEqual(planPanelState(completed), {
    plan: completed,
    label: "2/2",
  });
  assert.equal(normalizePlanSnapshot({ items: [] }), null);

  const planPosition = workspaceTemplate.indexOf('id="composerPlan"');
  const inputPosition = workspaceTemplate.indexOf('class="composer-inner"');
  assert.ok(planPosition >= 0 && planPosition < inputPosition);
  assert.doesNotMatch(chatSource, /wrap\.insertBefore\(planBlock, processBlock\)/);
  assert.match(chatSource, /showComposerPlanPlaceholder\(data\.tool_call_id\)/);
  assert.match(chatSource, /renderComposerPlan\(data\.plan\)/);
  assert.match(chatSource, /item\.kind !== "plan"/);
  assert.match(workspaceCss, /\.composer-plan\.open \.composer-plan-items \{ display: grid; \}/);
  assert.match(workspaceCss, /\.composer-plan \{[\s\S]*position: absolute;[\s\S]*bottom: calc\(100% \+ 10px\);/);
  assert.match(workspaceCss, /width: min\(420px, calc\(100% - 48px\)\);/);
  assert.match(workspaceCss, /\.composer \{ position: relative;/);
  const planRule = workspaceCss.match(/\.composer-plan \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.doesNotMatch(planRule, /box-shadow/);
  assert.match(chatSource, /case "final":[\s\S]*?clearComposerPlan\(\)/);
  assert.doesNotMatch(chatSource, /markComposerPlanTerminal|回合结束/);
  assert.match(chatSource, /历史会话只保留其事实和轨迹，不恢复浮层/);
});
