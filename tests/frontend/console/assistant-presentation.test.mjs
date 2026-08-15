import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildHistoryVisualItems,
  isPlanningToolEvent,
  latestAssistantRunPlan,
  normalizePlanSnapshot,
  planPanelState,
  processDisclosureLabel,
  splitAssistantHistoryRecords,
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

test("连续 assistant 历史按 user turn 合并为一个可视回答", () => {
  const items = buildHistoryVisualItems([
    { role: "user", content: "问题一", branch_point: 0 },
    { role: "assistant", parts: [{ type: "text", content: "中间" }], branch_point: 1 },
    { role: "assistant", parts: [{ type: "text", content: "最终" }], branch_point: 2 },
    { role: "user", content: "问题二", branch_point: 3 },
    { role: "assistant", parts: [{ type: "text", content: "回答二" }], branch_point: 4 },
  ]);

  assert.deepEqual(items.map((item) => item.type), ["user", "assistant", "user", "assistant"]);
  assert.equal(items[1].records.length, 2);
  assert.equal(items[3].records.length, 1);
});

test("Checkpoint 边界会截断连续 assistant 合并", () => {
  const items = buildHistoryVisualItems([
    { role: "user", content: "问题", branch_point: 0 },
    { role: "assistant", parts: [{ type: "text", content: "边界前" }], branch_point: 1 },
    { role: "assistant", parts: [{ type: "text", content: "边界后" }], branch_point: 4 },
  ], { source_last_sequence: 2 });

  assert.deepEqual(items.map((item) => item.type), ["user", "assistant", "checkpoint", "assistant"]);
  assert.equal(items[1].records[0].parts[0].content, "边界前");
  assert.equal(items[3].records[0].parts[0].content, "边界后");
});

test("最终文本常显，之前文本、thinking 和工具进入处理过程", () => {
  const split = splitAssistantHistoryRecords([
    {
      role: "assistant",
      parts: [
        { type: "thinking", content: "先分析" },
        { type: "text", content: "我先查一下" },
        { type: "tool_call", tool: "lookup", tool_call_id: "call-1", args: {} },
        { type: "tool_result", tool: "lookup", tool_call_id: "call-1", result: { ok: true } },
      ],
    },
    {
      role: "assistant",
      parts: [
        { type: "text", content: "过渡说明" },
        { type: "text", content: "这是最终答案" },
      ],
    },
  ]);

  assert.equal(split.finalText, "这是最终答案");
  assert.deepEqual(
    split.processParts.map((part) => part.type),
    ["thinking", "text", "tool_call", "tool_result", "text"],
  );
});

test("尾部仍是 Pending 工具时不伪造最终答案，interrupted 状态保留", () => {
  const split = splitAssistantHistoryRecords([
    {
      role: "assistant",
      status: "interrupted",
      parts: [
        { type: "text", content: "尚未完成" },
        { type: "tool_call", tool: "builtin__confirm", tool_call_id: "pending-1", args: { message: "继续？" } },
      ],
    },
  ]);

  assert.equal(split.finalText, "");
  assert.equal(split.interrupted, true);
  assert.deepEqual(split.processParts.map((part) => part.type), ["text", "tool_call"]);
});

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

test("任务计划只在消息区底部浮层展示最新未完成快照", () => {
  assert.equal(isPlanningToolEvent({ source: "planning", tool: "anything" }), true);
  assert.equal(isPlanningToolEvent({ tool: "write_plan" }), true);
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
    terminalStatus: null,
  });

  const completed = normalizePlanSnapshot({
    items: [
      { id: "1", content: "读取输入", status: "completed" },
      { id: "2", content: "生成结果", status: "completed" },
    ],
  });
  assert.equal(planPanelState(completed), null);
  assert.equal(planPanelState(running, "interrupted").label, "0/2 · 已中断");
  assert.equal(planPanelState(running, "error").label, "0/2 · 执行失败");
  assert.equal(planPanelState(running, "pending").label, "0/2 · 等待操作");
  assert.equal(normalizePlanSnapshot({ items: [] }), null);

  const planPosition = workspaceTemplate.indexOf('id="composerPlan"');
  const inputPosition = workspaceTemplate.indexOf('class="composer-inner"');
  assert.ok(planPosition >= 0 && planPosition < inputPosition);
  assert.doesNotMatch(chatSource, /wrap\.insertBefore\(planBlock, processBlock\)/);
  assert.match(chatSource, /showComposerPlanPlaceholder\(data\.tool_call_id\)/);
  assert.match(chatSource, /renderComposerPlan\(data\.plan\)/);
  assert.match(chatSource, /if \(isPlanningToolEvent\(part\)\) continue/);
  assert.match(workspaceCss, /\.composer-plan\.open \.composer-plan-items \{ display: grid; \}/);
  assert.match(workspaceCss, /\.composer-plan \{[\s\S]*position: absolute;[\s\S]*bottom: calc\(100% \+ 10px\);/);
  assert.match(workspaceCss, /width: min\(420px, calc\(100% - 48px\)\);/);
  assert.match(workspaceCss, /\.composer \{ position: relative;/);
});

test("历史只恢复最新 Assistant Run 的最后一份未完成计划", () => {
  const older = {
    items: [{ id: "old", content: "旧任务", status: "pending" }],
  };
  const first = {
    items: [
      { id: "a", content: "第一步", status: "completed" },
      { id: "b", content: "第二步", status: "pending" },
    ],
  };
  const latest = {
    items: [
      { id: "a", content: "第一步", status: "completed" },
      { id: "b", content: "第二步", active_form: "正在执行第二步", status: "in_progress" },
    ],
  };
  const planningResult = (plan, id) => ({
    type: "tool_result",
    tool: "update_task_status",
    source: "planning",
    tool_call_id: id,
    plan,
  });
  const messages = [
    { role: "user", content: "旧任务" },
    { role: "assistant", run_id: "run-old", status: "interrupted", parts: [planningResult(older, "old")] },
    { role: "user", content: "新任务" },
    { role: "assistant", run_id: "run-new", status: "completed", parts: [planningResult(first, "1")] },
    { role: "assistant", run_id: "run-new", status: "interrupted", parts: [planningResult(latest, "2")] },
  ];
  assert.deepEqual(latestAssistantRunPlan(messages), {
    snapshot: latest,
    runId: "run-new",
    terminalStatus: "interrupted",
  });

  assert.equal(latestAssistantRunPlan([...messages, { role: "user", content: "收到" }]), null);
  assert.equal(latestAssistantRunPlan([
    ...messages,
    { role: "user", content: "收到" },
    { role: "assistant", run_id: "run-plain", status: "completed", parts: [{ type: "text", content: "收到" }] },
  ]), null);

  const complete = {
    items: [
      { id: "a", content: "第一步", status: "completed" },
      { id: "b", content: "第二步", status: "completed" },
    ],
  };
  const restored = latestAssistantRunPlan([
    { role: "user", content: "完成任务" },
    { role: "assistant", run_id: "run-complete", status: "completed", parts: [planningResult(complete, "3")] },
  ]);
  assert.deepEqual(restored?.snapshot, complete);
  assert.equal(planPanelState(restored?.snapshot, restored?.terminalStatus), null);

  assert.deepEqual(latestAssistantRunPlan([
    { role: "user", content: "兼容历史" },
    { role: "assistant", status: "completed", parts: [planningResult(first, "legacy-1")] },
    { role: "assistant", status: "interrupted", parts: [planningResult(latest, "legacy-2")] },
  ]), {
    snapshot: latest,
    runId: null,
    terminalStatus: "interrupted",
  });
});
