import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  applyAssistantPresentationActions,
  assistantActionsFromDeferred,
  assistantActionsFromHistoryItems,
  assistantActionsFromSse,
  formatProcessElapsed,
  isPlanningToolEvent,
  normalizePlanSnapshot,
  planPanelState,
  processDisclosureLabel,
} from "../../../console/assistant-presentation.js";

function recordingPresenter() {
  const calls = [];
  const presenter = {};
  for (const method of [
    "appendThinking",
    "addThinkingBlock",
    "finalizeThinking",
    "appendText",
    "setFinalText",
    "addProcessText",
    "finalizeText",
    "addToolCall",
    "addToolResult",
    "removeToolCall",
    "attachHitlWidget",
    "complete",
    "markInterrupted",
  ]) {
    presenter[method] = (...args) => calls.push([method, ...args]);
  }
  return { presenter, calls };
}

const chatSource = await readPresentationSource("chat.js");
const workspaceCss = await readFile(
  new URL("../../../console/workspace.css", import.meta.url),
  "utf8",
);
const workspaceTemplate = await readFile(
  new URL("../../../console/workspace-template.html", import.meta.url),
  "utf8",
);
const themesCss = await readFile(
  new URL("../../../console/themes.css", import.meta.url),
  "utf8",
);

test("历史过程和普通工具默认折叠，等待操作标题清晰", () => {
  assert.equal(processDisclosureLabel(), "已处理");
  assert.equal(processDisclosureLabel({ running: true }), "处理中");
  assert.equal(processDisclosureLabel({ pending: true }), "等待操作");
  assert.match(chatSource, /processBlock\.className = "assistant-process hidden"/);
  assert.match(chatSource, /isSkillLoad = merged\.tool === "load_capability"/);
  assert.match(chatSource, /const defaultTitle = merged\.display_name \|\| merged\.tool \|\| "tool"/);
  assert.match(chatSource, /block\.classList\.add\("hitl-pending"\)/);
  assert.doesNotMatch(chatSource, /block\.classList\.add\("open", "hitl-pending"\)/);
  assert.match(chatSource, /textContent = `等待操作 · \$\{defaultTitle\}`/);
});

test("实时处理过程展开计时并在最终结果后折叠", () => {
  assert.equal(formatProcessElapsed(8), "8秒");
  assert.equal(formatProcessElapsed(72), "1分12秒");
  assert.equal(processDisclosureLabel({ running: true, elapsedSeconds: 72 }), "处理中 · 1分12秒");
  assert.equal(processDisclosureLabel({ elapsedSeconds: 72 }), "已处理 · 用时 1分12秒");
  assert.match(chatSource, /let isLive = !readOnly && !timestamp/);
  assert.match(chatSource, /processElapsedSecondsBetween\([\s\S]*processTiming\.startedAt[\s\S]*processTiming\.completedAt/);
  assert.match(chatSource, /processDisclosureLabel\(\{[\s\S]*elapsedSeconds: processElapsedSeconds/);
  assert.match(chatSource, /window\.setInterval\(updateProcessTimer, 1000\)/);
  assert.match(chatSource, /processBlock\.classList\.add\("open"\)/);
  assert.match(chatSource, /processBlock\.classList\.remove\("open", "running"\)/);
  assert.match(chatSource, /complete\(output\)[\s\S]*finishProcess\(\)/);
  assert.match(chatSource, /resumeProcess\(\) \{[\s\S]*startProcess\(\)/);
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
  const thinkingHeader = chatSource.match(/<button type="button" class="assistant-thinking-head"[\s\S]*?<\/button>/)[0];
  assert.match(thinkingHeader, /thinking-icon[\s\S]*lucideIcon\("brain"\)[\s\S]*thinking-title[\s\S]*thinking-chevron/);
  assert.match(thinkingHeader, /class="spinner" role="status" aria-label="思考中"/);
  assert.doesNotMatch(workspaceCss, /\.assistant-thinking \{[^}]*border-left:/);
  assert.match(
    workspaceCss,
    /\.assistant-thinking-body \{[^}]*padding: 8px 0 8px 20px;[^}]*border-left: 2px solid var\(--accent-soft\);/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-thinking-head \{[^}]*min-height: 32px;/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-thinking \.thinking-content \{[\s\S]*?max-height: 50vh;[\s\S]*?padding: 0;[\s\S]*?line-height: 1\.6;/,
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
    /\.assistant-process \{[\s\S]*?margin: 1px 0 0;[\s\S]*?border: 0;/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-process-header \{[\s\S]*?width: 100%;[\s\S]*?border-bottom: 1px solid var\(--border-soft\);/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-process-summary \{[\s\S]*?width: fit-content;[\s\S]*?display: inline-flex;/,
  );
  assert.match(workspaceCss, /\.assistant-process-icon \{ display: none; \}/);
  assert.match(
    workspaceCss,
    /\.assistant-process-body \{[\s\S]*?gap: 4px;[\s\S]*?padding: 4px 0 6px;/,
  );
  assert.match(
    workspaceCss,
    /\.assistant-process-text \{[\s\S]*?padding: 0;[\s\S]*?color: var\(--text\);[\s\S]*?background: transparent;[\s\S]*?border: 0;/,
  );
  assert.match(
    workspaceCss,
    /:is\(\.assistant-final, \.assistant-process-text\) \.markdown-body > :first-child \{ margin-top: 0; \}/,
  );
  assert.match(
    workspaceCss,
    /:is\(\.assistant-final, \.assistant-process-text\) \.markdown-body > :last-child \{ margin-bottom: 0; \}/,
  );
  assert.match(workspaceCss, /\.tool-search-block \{[^}]*background: transparent;[^}]*border: 0;/);
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

test("用户与 Agent 正文底部统一悬停展示复制和时间", () => {
  assert.match(chatSource, /function createMessageActions\(\{/);
  assert.match(chatSource, /timeEl\.className = "msg-time"/);
  assert.match(chatSource, /getText: text \? \(\) => text : null/);
  assert.match(chatSource, /getText: \(\) => textBuffer/);
  assert.doesNotMatch(chatSource, /prependMsgTime/);
  assert.match(
    workspaceCss,
    /\.msg-row\.user:hover \.msg-actions,[\s\S]*?\.msg-row\.assistant:hover \.msg-actions,[\s\S]*?opacity: 1;/,
  );
  assert.match(workspaceCss, /\.assistant-answer \.assistant-message \{[^}]*gap: 6px;/);
  assert.match(workspaceCss, /:is\(\.assistant-final, \.assistant-process-text\) \.markdown-body > :first-child \{ margin-top: 0; \}/);
  assert.match(workspaceCss, /:is\(\.assistant-final, \.assistant-process-text\) \.markdown-body > :last-child \{ margin-bottom: 0; \}/);
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
  assert.match(chatSource, /case "final":[\s\S]*assistantActionsFromSse\(type, data\)/);
  assert.match(chatSource, /case "cancelled":[\s\S]*assistantActionsFromSse\(type, data\)/);
  assert.match(chatSource, /case "error":[\s\S]*addErrorTo\(assistant/);
});

test("History 与 SSE 通过同一动作入口驱动思考、正文和工具", () => {
  const historyActions = assistantActionsFromHistoryItems([
    {
      kind: "assistant_message",
      thinking: "先核对",
      text: "正在处理",
      phase: "process",
      status: "completed",
    },
    {
      kind: "tool",
      call_id: "call-1",
      tool_name: "lookup",
      display_name: "查询数据",
      input: { region: "华东" },
      output: { count: 2 },
      status: "completed",
      artifacts: [{ id: "artifact-1" }],
    },
    {
      kind: "assistant_message",
      text: "核对完成",
      phase: "final",
      status: "completed",
    },
  ]);
  const liveActions = [
    ...assistantActionsFromSse("thinking", { delta: "先核对" }),
    ...assistantActionsFromSse("text", { delta: "正在处理" }),
    ...assistantActionsFromSse("tool_call", {
      tool_call_id: "call-1",
      tool: "lookup",
      display_name: "查询数据",
      args: { region: "华东" },
    }),
    ...assistantActionsFromSse("tool_result", {
      tool_call_id: "call-1",
      tool: "lookup",
      display_name: "查询数据",
      result: { count: 2 },
      artifacts: [{ id: "artifact-1" }],
    }),
    ...assistantActionsFromSse("final", { output: "核对完成" }),
  ];

  const historyRecorder = recordingPresenter();
  const liveRecorder = recordingPresenter();
  applyAssistantPresentationActions(historyRecorder.presenter, historyActions);
  applyAssistantPresentationActions(liveRecorder.presenter, liveActions);

  assert.ok(historyRecorder.calls.some(([method]) => method === "addThinkingBlock"));
  assert.ok(liveRecorder.calls.some(([method]) => method === "appendThinking"));
  assert.equal(historyRecorder.calls.filter(([method]) => method === "addToolCall").length, 1);
  assert.equal(liveRecorder.calls.filter(([method]) => method === "addToolCall").length, 1);
  assert.equal(historyRecorder.calls.filter(([method]) => method === "addToolResult").length, 1);
  assert.equal(liveRecorder.calls.filter(([method]) => method === "addToolResult").length, 1);
  assert.equal(
    historyRecorder.calls.find(([method]) => method === "addToolCall")[1].display_name,
    "查询数据",
  );
  assert.equal(
    liveRecorder.calls.find(([method]) => method === "addToolCall")[1].display_name,
    "查询数据",
  );
  assert.ok(historyRecorder.calls.some(([method, value]) => method === "setFinalText" && value === "核对完成"));
  assert.ok(liveRecorder.calls.some(([method, value]) => method === "complete" && value === "核对完成"));
});

test("原生搜索实时文本和工具交错后仍由 final 输出完成正文", () => {
  const recorder = recordingPresenter();
  const frames = [
    ["thinking", { item_id: "message:a:0", delta: "先核对来源" }],
    ["text", { item_id: "message:a:1", delta: "正在查找" }],
    ["tool_call", {
      item_id: "tool:search-1", tool_call_id: "search-1", tool: "web_search",
      source: "web", execution: "native", args: { query: "测试查询" },
    }],
    ["tool_result", {
      item_id: "tool:search-1", tool_call_id: "search-1", tool: "web_search",
      source: "web", execution: "native", result: { sources: [] },
    }],
    ["text", { item_id: "message:a:5", delta: "最后的" }],
    ["text", { item_id: "message:a:5", delta: "完整回答。" }],
    ["final", { status: "completed", output: "最后的完整回答。" }],
  ];
  for (const [type, data] of frames) {
    applyAssistantPresentationActions(recorder.presenter, assistantActionsFromSse(type, data));
  }

  assert.deepEqual(
    recorder.calls.filter(([method]) => ["appendText", "complete"].includes(method)),
    [
      ["appendText", "正在查找"],
      ["appendText", "最后的"],
      ["appendText", "完整回答。"],
      ["complete", "最后的完整回答。"],
    ],
  );
  assert.equal(recorder.calls.filter(([method]) => method === "addToolCall").length, 1);
  assert.equal(recorder.calls.filter(([method]) => method === "addToolResult").length, 1);
  assert.equal(recorder.calls.find(([method]) => method === "addToolCall")[1].execution, "native");
  assert.deepEqual(recorder.calls.at(-1), ["complete", "最后的完整回答。"]);
});

test("展示动作覆盖多工具、撤回、失败、中断与 Deferred", () => {
  const historyActions = assistantActionsFromHistoryItems([
    {
      kind: "tool",
      call_id: "call-failed",
      tool_name: "failed_tool",
      input: { id: 1 },
      output: { error: "failed" },
      status: "failed",
    },
    {
      kind: "tool",
      call_id: "call-deferred",
      tool_name: "builtin__confirm",
      input: { message: "继续？" },
      status: "awaiting_input",
      widget: "confirm",
    },
    {
      kind: "assistant_message",
      text: "部分结果",
      phase: "final",
      status: "interrupted",
    },
  ]);
  const recorder = recordingPresenter();
  applyAssistantPresentationActions(recorder.presenter, historyActions);
  applyAssistantPresentationActions(
    recorder.presenter,
    assistantActionsFromSse("tool_call_removed", { tool_call_id: "call-retry" }),
  );
  applyAssistantPresentationActions(
    recorder.presenter,
    assistantActionsFromDeferred({ calls: [{ tool_call_id: "call-next" }] }),
  );

  assert.equal(recorder.calls.filter(([method]) => method === "addToolCall").length, 2);
  assert.equal(recorder.calls.filter(([method]) => method === "addToolResult").length, 1);
  assert.equal(recorder.calls.filter(([method]) => method === "attachHitlWidget").length, 2);
  assert.ok(recorder.calls.some(([method]) => method === "removeToolCall"));
  assert.ok(recorder.calls.some(([method]) => method === "markInterrupted"));
});

test("History 动作不把已撤回工具当作完成结果展示", () => {
  const actions = assistantActionsFromHistoryItems([
    { kind: "tool", call_id: "retry-call", tool_name: "read_file", status: "removed", output: "unsupported" },
    { kind: "tool", call_id: "document-call", tool_name: "parse_document", status: "pending" },
  ]);
  const recorder = recordingPresenter();
  applyAssistantPresentationActions(recorder.presenter, actions);
  assert.deepEqual(recorder.calls.filter(([method]) => method === "addToolCall").map(([, call]) => call.tool_call_id), ["document-call"]);
  assert.equal(recorder.calls.filter(([method]) => method === "addToolResult").length, 0);
});

test("共享动作入口恢复 Deferred 时仍补齐 UI 工具元数据", () => {
  const recorder = recordingPresenter();
  applyAssistantPresentationActions(
    recorder.presenter,
    assistantActionsFromDeferred({ calls: [{ tool: "confirm" }] }),
    { prepareDeferredCall: (call) => ({ ...call, widget: "confirmation" }) },
  );

  assert.deepEqual(
    recorder.calls.find(([method]) => method === "attachHitlWidget"),
    ["attachHitlWidget", { tool: "confirm", widget: "confirmation" }],
  );
});

test("大工具结果显示外置提示并保留预览", () => {
  assert.match(chatSource, /renderToolResultArtifacts\(data\.artifacts\)/);
  assert.match(chatSource, /完整结果已外置/);
  assert.match(chatSource, /Agent 可按需读取/);
  assert.match(workspaceCss, /\.tool-result-artifact/);
});

test("无参数工具显示空对象且参数和结果 JSON 不重复绘制边框", () => {
  assert.match(chatSource, /const renderedArgs = hasArgs \? merged\.args : \{\};/);
  assert.match(chatSource, /renderBlock\(renderedArgs, \{ className: "args", ariaLabel: "工具参数 JSON" \}\)/);
  assert.doesNotMatch(chatSource, /参数接收中…|args-pending/);
  assert.match(
    workspaceCss,
    /\.tool-body > \.json-view\.args,[\s\S]*?\.tool-body \[data-tool-result\] > \.json-view\s*\{[\s\S]*?border:\s*0;/,
  );
});

test("Python 生成文件显示文件信息、下载与可复制 File ID", () => {
  assert.match(chatSource, /item\?\.kind === "sandbox_output"/);
  assert.match(chatSource, /renderGeneratedFileArtifact/);
  assert.match(chatSource, /data-copy-file-id/);
  assert.match(chatSource, /api\/v1\/local-files/);
  assert.match(workspaceCss, /\.generated-file-item/);
  assert.match(workspaceCss, /\.generated-file-actions/);
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
  assert.doesNotMatch(chatSource, /item\.kind !== "plan"/);
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
