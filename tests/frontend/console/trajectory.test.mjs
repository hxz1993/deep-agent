import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildTrajectory,
  filterTrajectory,
  redactTrajectoryValue,
  runCodeSource,
  renderPtcSource,
  shouldIngestTrajectoryEvent,
  trajectoryStats,
} from "../../../console/trajectory.js";

test("远程后台完成结果合并到原工具轨迹，补查取件只更新同一行", () => {
  const call = {
    event_id: "call-event", event_type: "assistant/message", sequence: 1, run_id: "run",
    payload: { message: { parts: [{
      part_kind: "tool-call", tool_name: "delegate_task", tool_call_id: "call", args: { task: "Review" },
    }] } },
  };
  const receipt = {
    event_id: "receipt", event_type: "tool/results", sequence: 2, run_id: "run",
    payload: { message: { parts: [{
      part_kind: "tool-return", tool_name: "delegate_task", tool_call_id: "call", content: "Background receipt",
    }] } },
  };
  const invocation = {
    event_id: "completed", event_type: "remote_agent/invocation", sequence: 3, status: "completed",
    payload: { id: "task", run_id: "run", tool_call_id: "call", message_id: "step",
      name: "Remote", protocol: "a2a", text: "Review done", warnings: [], file_message_ids: { result: "step" },
      files: { result: { file_id: "local_result", file_name: "result.csv" } } },
  };
  const refreshed = {
    ...invocation, event_id: "refreshed", sequence: 4,
    payload: { ...invocation.payload, files: { ...invocation.payload.files,
      after_retry: { file_id: "local_retry", file_name: "retry.csv" },
      previous: { file_id: "local_previous", file_name: "old.csv" } },
      file_message_ids: { result: "step", after_retry: "step", previous: "older-step" } },
  };
  const tools = buildTrajectory({ items: [call, receipt, invocation, refreshed] })
    .filter(row => row.kind === "tool");
  assert.equal(tools.length, 1);
  assert.equal(tools[0].status, "completed");
  assert.equal(tools[0].result.result.result, "Review done");
  assert.deepEqual(tools[0].result.artifacts.map(file => file.file_id), ["local_result", "local_retry"]);
  assert.equal(tools[0].native.result_event_id, "refreshed");
});

test("PTC 精确源码按 UTF-8 范围定位同一行的单次调用，并回读原始片段", () => {
  const code = '# 中文😀\na = query(1); b = query(2)\n';
  const original = { kind: "tool", label: "run_code", tool_call_id: "outer-1", run_id: "run-1", payload: { args: { code } } };
  const expression = "query(2)";
  const start = new TextEncoder().encode(code.slice(0, code.indexOf(expression))).length;
  const step = { tool_name: "query", source_location: { call_id: "outer-1", filename: "<python-input-0>", start, end: start + expression.length } };
  const html = renderPtcSource(original, step);
  assert.match(html, /源码：第 2 行/);
  assert.equal((html.match(/<mark /g) || []).length, 1);
  assert.match(html, /<mark class="trajectory-code-call">query\(2\)<\/mark>/);
  assert.doesNotMatch(html, /<mark[^>]*>query\(1\)/);
  const current = { ...original, tool_call_id: "outer-2", payload: { args: { code: "await helper()" } } };
  assert.match(renderPtcSource(current, step, [original]), /先前 PTC 调用/);
  assert.match(renderPtcSource(current, step, [original]), /query\(2\)/);
  assert.doesNotMatch(renderPtcSource(current, step, [{ ...original, run_id: "other-run" }]), /<mark/);
  assert.match(renderPtcSource(current, step), /尚未加载/);
});

test("PTC 多行源码、旧历史和损坏位置安全回退，不把定位当作报错", () => {
  const code = 'result = await query(\n    "<script>",\n    2\n)';
  const event = { kind: "tool", label: "run_code", tool_call_id: "outer", payload: { args: { code } } };
  const start = new TextEncoder().encode('result = await ').length;
  const step = { tool_name: "query", source_location: { call_id: "outer", start, end: new TextEncoder().encode(code).length } };
  const html = renderPtcSource(event, step);
  assert.match(html, /第 1–4 行/);
  assert.equal((html.match(/<mark /g) || []).length, 4);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|is-error/);
  assert.match(renderPtcSource(event, { tool_name: "query" }), /is-call-match/);
  for (const bad of [{ start: -1 }, { start: 2.5 }, { end: 10000 }, { start: true }]) {
    assert.match(renderPtcSource(event, { ...step, source_location: { ...step.source_location, ...bad } }), /定位信息不可用/);
  }
  const chinese = { ...event, payload: { args: { code: "中文" } } };
  assert.match(renderPtcSource(chinese, { source_location: { call_id: "outer", start: 1, end: 3 } }), /定位信息不可用/);
});

const template = await readFile(
  new URL("../../../console/workspace-template.html", import.meta.url),
  "utf8",
);
const chatSource = await readPresentationSource("chat.js");
const workspaceCss = await readFile(
  new URL("../../../console/workspace.css", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);
const configurationCss = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const themesCss = await readFile(
  new URL("../../../console/themes.css", import.meta.url),
  "utf8",
);
const trajectorySource = await readFile(
  new URL("../../../console/trajectory.js", import.meta.url),
  "utf8",
);
const jsonViewSource = await readFile(
  new URL("../../../console/json-view.js", import.meta.url),
  "utf8",
);

const history = {
  session_id: "session-1",
  items: [
    {
      event_id: "evt-user",
      sequence: 1,
      event_type: "user/message",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: {
        message: { kind: "request", parts: [{
          part_kind: "user-prompt",
          content: [
            { kind: "text-content", content: "查询收入" },
            { kind: "attachment-ref", id: "file-1", name: "input.xlsx" },
            { kind: "context-ref", key: "current_range", type: "deeptable.range", schema_version: "1.0", label: "预算表!B2:F10" },
          ],
        }] },
      },
      created_at: "2026-08-23T10:00:00Z",
      updated_at: "2026-08-23T10:00:00Z",
    },
    {
      event_id: "evt-title-fallback",
      sequence: 2,
      event_type: "session/title",
      status: "running",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: { title: "查询收入", source: "fallback", source_event_id: "evt-user" },
      created_at: "2026-08-23T10:00:00Z",
      updated_at: "2026-08-23T10:00:00Z",
    },
    {
      event_id: "evt-title-model",
      sequence: 3,
      event_type: "session/title",
      status: "completed",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: { title: "收入查询分析", source: "model", source_event_id: "evt-user", model_name: "title-model" },
      created_at: "2026-08-23T10:00:00Z",
      updated_at: "2026-08-23T10:00:01Z",
    },
    {
      event_id: "evt-assistant-tool",
      sequence: 4,
      event_type: "assistant/message",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: {
        message: {
          kind: "response",
          parts: [
            { part_kind: "thinking", content: "先查工具" },
            { part_kind: "tool-call", tool_name: "cube_query", tool_call_id: "call-1", args: { cube: "Sales", access_token: "private" } },
          ],
          model_name: "deepseek-v4-flash",
          provider_name: "openai-compatible",
          provider_response_id: "response-1",
          finish_reason: "tool_call",
          usage: { input_tokens: 20, output_tokens: 4 },
        },
      },
      created_at: "2026-08-23T10:00:01Z",
      updated_at: "2026-08-23T10:00:01Z",
    },
    {
      event_id: "evt-result",
      sequence: 5,
      event_type: "tool/results",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: { message: { kind: "request", parts: [{ part_kind: "tool-return", tool_name: "cube_query", tool_call_id: "call-1", content: { rows: 2 }, outcome: "success" }] } },
      created_at: "2026-08-23T10:00:02Z",
      updated_at: "2026-08-23T10:00:02Z",
    },
    {
      event_id: "evt-final",
      sequence: 6,
      event_type: "assistant/message",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: { message: { kind: "response", parts: [{ part_kind: "text", content: "查询完成" }] } },
      created_at: "2026-08-23T10:00:03Z",
      updated_at: "2026-08-23T10:00:03Z",
    },
    {
      event_id: "evt-compaction",
      sequence: 7,
      event_type: "compaction",
      status: "completed",
      turn_id: "turn-1",
      run_id: "run-1",
      payload: { source: { through_sequence: 5 }, budget: { estimated_before: 9000, estimated_after: 4000 }, summary_call: { model_id: "summary-model" }, duration_ms: 4321 },
      created_at: "2026-08-23T10:00:04Z",
      updated_at: "2026-08-23T10:00:05Z",
    },
  ],
};

test("完整 Message 投影合并思考正文与工具调用结果", () => {
  const events = buildTrajectory(history);
  const call = events.find((event) => event.id === "tool:call-1");
  const assistant = events.find((event) => event.id === "evt-final");
  const thinking = events.find((event) => event.id === "evt-assistant-tool");
  const input = events.find((event) => event.kind === "input");
  const title = events.find((event) => event.kind === "title");

  assert.equal(call.status, "completed");
  assert.deepEqual(call.result.result, { rows: 2 });
  assert.equal(call.payload.args.access_token, "[已隐藏]");
  assert.equal(thinking.native.model_name, "deepseek-v4-flash");
  assert.equal(thinking.native.usage.input_tokens, 20);
  assert.equal(thinking.kind, "assistant");
  assert.equal(thinking.payload.thinking, "先查工具");
  assert.equal(assistant.payload.content, "查询完成");
  assert.equal(input.payload.context_refs[0].type, "deeptable.range");
  assert.equal(input.payload.attachments[0].name, "input.xlsx");
  assert.equal(input.summary, "查询收入");
  assert.equal(input.run_id, "run-1");
  assert.equal(title.id, "evt-title-fallback");
  assert.equal(title.summary, "收入查询分析");
  assert.equal(title.payload.source, "model");
  assert.deepEqual(title.native.event_ids, ["evt-title-fallback", "evt-title-model"]);
  assert.deepEqual(events.map((event) => event.id), ["evt-user", "evt-title-fallback", "evt-assistant-tool", "tool:call-1", "evt-final", "evt-compaction"]);
  assert.equal(events.at(-1).kind, "compaction");
  assert.equal(events.at(-1).sequence, 7);
  assert.equal(events.at(-1).duration_ms, 4321);
  assert.equal(call.payload.args.cube, "Sales");
});

test("轨迹筛选、统计与脱敏保持确定性", () => {
  const events = buildTrajectory(history);
  assert.deepEqual(trajectoryStats(events), { inputs: 1, calls: 1, runs: 1 });
  assert.equal(filterTrajectory(events, { mode: "calls" }).length, 1);
  assert.equal(filterTrajectory(events, { query: "查询完成" }).length, 1);
  assert.equal(filterTrajectory(events, { query: "预算表!B2:F10" }).length, 1);
  assert.deepEqual(
    redactTrajectoryValue({ authorization: "a", nested: { api_key: "b", safe: 1 } }),
    { authorization: "[已隐藏]", nested: { api_key: "[已隐藏]", safe: 1 } },
  );
});

test("真实 Pydantic 工具参数字符串可提取 run_code Python 源码", () => {
  const code = 'payload = await load_file_data(file_id="local_1")\nawait upload_media(base64_content=payload["content"])';
  const projected = buildTrajectory({
    items: [{
      event_id: "evt-run-code",
      sequence: 1,
      event_type: "assistant/message",
      turn_id: "turn-code",
      run_id: "run-code",
      payload: {
        message: {
          kind: "response",
          parts: [{
            part_kind: "tool-call",
            tool_name: "run_code",
            tool_call_id: "call-code",
            args: JSON.stringify({ code }),
          }],
        },
      },
      created_at: "2026-08-28T08:00:00Z",
      updated_at: "2026-08-28T08:00:00Z",
    }],
  });
  const event = projected.find((item) => item.id === "tool:call-code");
  assert.equal(event.label, "run_code");
  assert.doesNotMatch(trajectorySource, /display_name/);
  assert.equal(runCodeSource(event), code);
  assert.equal(runCodeSource({ payload: { args: { code } } }), code);
  assert.equal(runCodeSource({ payload: { args: "not-json" } }), "");
});

test("Python 生成文件在轨迹中只保留安全引用", () => {
  const projected = buildTrajectory({
    items: [
      {
        event_id: "evt-python-call",
        sequence: 1,
        event_type: "assistant/message",
        run_id: "run-python",
        payload: { message: { kind: "response", parts: [{
          part_kind: "tool-call",
          tool_name: "execute_python",
          tool_call_id: "call-python",
          args: { code: "pass" },
        }] } },
        created_at: "2026-09-01T08:00:00Z",
        updated_at: "2026-09-01T08:00:00Z",
      },
      {
        event_id: "evt-python-result",
        sequence: 2,
        event_type: "tool/results",
        run_id: "run-python",
        payload: { message: { kind: "request", parts: [{
          part_kind: "tool-return",
          tool_name: "execute_python",
          tool_call_id: "call-python",
          content: { result: { ok: true }, files: [{ file_id: "local_generated", file_name: "chart.png" }] },
          metadata: { generated_files: [{
            file_id: "local_generated",
            file_name: "chart.png",
            mime: "image/png",
            byte_size: 2048,
            sha256: "abc",
            path: "/tmp/hidden.png",
            base64: "hidden",
          }] },
        }] } },
        created_at: "2026-09-01T08:00:01Z",
        updated_at: "2026-09-01T08:00:01Z",
      },
    ],
  });
  const event = projected.find((item) => item.id === "tool:call-python");
  assert.deepEqual(event.result.artifacts, [{
    artifact_id: "local_generated",
    file_id: "local_generated",
    kind: "sandbox_output",
    role: "generated_file",
    file_name: "chart.png",
    mime: "image/png",
    byte_size: 2048,
    sha256: "abc",
  }]);
  assert.doesNotMatch(JSON.stringify(event.result.artifacts), /\/tmp\/|base64|hidden/);
});

test("标题投影通知不进入语义轨迹", () => {
  assert.equal(shouldIngestTrajectoryEvent("session_title"), false);
  assert.equal(shouldIngestTrajectoryEvent("session_event_appended"), true);
  assert.equal(shouldIngestTrajectoryEvent("thinking"), true);
});

test("工作台以独立页签接入轨迹且默认对话链路不变", () => {
  assert.match(template, /class="chat-view-tab active" id="conversationViewTab"/);
  assert.match(template, /id="trajectoryViewTab"/);
  assert.match(template, /id="trajectoryView"[\s\S]*id="trajectoryList"/);
  assert.doesNotMatch(template, /trajectorySequence|会话事件顺序/);
  assert.doesNotMatch(template, /data-trajectory-mode="runs"|trajectoryRunCount/);
  assert.match(template, /data-trajectory-mode="calls"[\s\S]*data-lucide="wrench"/);
  assert.doesNotMatch(template, /id="trajectoryDetail"/);
  assert.match(chatSource, /createTrajectoryController\(\{ state \}\)/);
  assert.match(chatSource, /if \(events\.response\.ok\)\s*\{\s*trajectory\?\.setHistory\(events\.data\)/);
  assert.match(chatSource, /trajectory\?\.ingest\(\{ event: type, data \}\)/);
  assert.match(workspaceCss, /\.chat-view-tabs[\s\S]*border-radius:\s*8px/);
  assert.doesNotMatch(workspaceCss, /\.chat-view-tab\.active::after/);
  assert.match(workspaceCss, /\.trajectory-layout[\s\S]*display:\s*block/);
  assert.doesNotMatch(workspaceCss, /trajectory-timeline|trajectory-segment|trajectory-sequence/);
  assert.match(workspaceCss, /\.inspector-pane\.trajectory-inspector/);
  assert.match(workspaceCss, /\.trajectory-fact[^}]*grid-template-columns:\s*64px minmax\(0, 1fr\)/);
  assert.match(trajectorySource, /input: "用户"/);
  assert.doesNotMatch(trajectorySource, /kind === "context"|kind: "context"|renderContextSummary|parent_input_id/);
  assert.match(trajectorySource, /DeepAgentMarkdown\?\.renderBody/);
  assert.match(trajectorySource, /DeepAgentJson\?\.renderBlock/);
  assert.match(workspaceCss, /\.trajectory-summary-markdown/);
  assert.match(workspaceCss, /\.trajectory-thinking-content[^}]*max-height:\s*50vh/);
  assert.match(trajectorySource, /const preview = `\$\{renderThinking\(event\.payload\?\.thinking\)\}\$\{renderMarkdown/);
  assert.doesNotMatch(workspaceCss, /\.trajectory-summary-preview \.trajectory-thinking,[\s\S]*border-left:\s*2px solid var\(--accent-soft\)/);
  assert.doesNotMatch(workspaceCss, /trajectory-thinking-preview|trajectory-technical-details/);
  assert.doesNotMatch(trajectorySource, /标识与模型信息|renderTechnicalDetails/);
  assert.match(trajectorySource, /\["summary", "Summary"\]/);
  assert.match(trajectorySource, /tabs\.push\(\["message", "Message"\], \["preview", "Preview"\]\)/);
  assert.match(trajectorySource, /if \(trajectoryUsage\(event\)\) tabs.push\(\["usage", "Usage"\]\)/);
  assert.match(trajectorySource, /tabs.push\(\["raw", "Raw"\]\)/);
  assert.match(trajectorySource, /\["Thinking", event\.payload\?\.thinking\][\s\S]*\["Text", event\.payload\?\.content\]/);
  assert.match(trajectorySource, /completed: "Completed"/);
  assert.doesNotMatch(trajectorySource, /trajectoryRunCount/);
  assert.match(trajectorySource, /renderSummarySection\("Input"[\s\S]*renderSummarySection\("Result"/);
  assert.match(trajectorySource, /data-trajectory-detail-tab-target/);
  assert.match(trajectorySource, /data-trajectory-event-target/);
  assert.match(trajectorySource, /renderDetail\(selected, detailTab, events, attachmentUrl,/);
  assert.match(trajectorySource, /renderSummarySection\("Attachments"/);
  assert.match(trajectorySource, /renderSummarySection\("Context references"/);
  assert.doesNotMatch(trajectorySource, /Object\.entries\(reference\.payload/);
  assert.doesNotMatch(trajectorySource, /下载本地文件/);
  assert.match(trajectorySource, /trajectory-summary-link local-file-download/);
  assert.doesNotMatch(trajectorySource, /icon\("list-checks"\)|icon\("archive-restore"\)|icon\("message-square-more"\)/);
  assert.match(trajectorySource, /fileId\.startsWith\("local_"\)/);
  assert.match(workspaceCss, /\.local-file-download\s*\{/);
  assert.match(trajectorySource, /data-copy-file-id/);
  assert.match(trajectorySource, /renderGeneratedFiles\(artifacts, attachmentUrl\)/);
  assert.match(trajectorySource, /metadata\.generated_files/);
  assert.match(workspaceCss, /\.generated-file-list\s*\{/);
  assert.doesNotMatch(trajectorySource, /\["timing", "Timing"\]/);
  assert.doesNotMatch(trajectorySource, /renderFact\("Role"|trajectory-preview-message|eventRole/);
  assert.doesNotMatch(trajectorySource, /trajectoryTimelineLayout|renderSequence|renderRowSignals|iconForKind/);
  assert.doesNotMatch(trajectorySource, /含思考|请求调用|个调用/);
  assert.match(workspaceCss, /\.trajectory-list\s*\{[\s\S]*overflow-x:\s*hidden/);
  assert.match(workspaceCss, /\.trajectory-toolbar\s*\{[^}]*border-bottom:\s*0;/);
  assert.match(workspaceCss, /\.trajectory-group\s*\{[^}]*border-bottom:\s*0;/);
  assert.match(workspaceCss, /\.trajectory-row\s*\{[^}]*border-bottom:\s*0;/);
  assert.match(workspaceCss, /\.trajectory-summary-meta\s*\{[^}]*border-bottom:\s*0;/);
  assert.match(workspaceCss, /\.inspector-pane\.trajectory-inspector \.inspector-content\s*\{[^}]*overflow:\s*hidden;/);
  assert.match(workspaceCss, /\.trajectory-detail-tabs\s*\{[^}]*overflow-x:\s*auto;/);
  assert.match(workspaceCss, /\.trajectory-detail-body\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*overflow:\s*auto;/);
  assert.match(workspaceCss, /\.trajectory-summary-preview\.is-scroll \.trajectory-summary-section-body\s*\{[\s\S]*max-height:\s*180px;[\s\S]*overflow-y:\s*auto/);
  assert.doesNotMatch(workspaceCss, /\.trajectory-summary-preview\.is-scroll \.trajectory-summary-section-body\s*\{[^}]*\n\s*height:\s*180px/);
  assert.match(workspaceCss, /\.trajectory-export\s*\{[\s\S]*height:\s*32px;[\s\S]*min-height:\s*32px/);
  assert.match(workspaceCss, /\.trajectory-detail-body \.trajectory-json\.is-flat\s*\{[\s\S]*border:\s*0;[\s\S]*background:\s*transparent/);
  assert.match(workspaceCss, /\.trajectory-kind\.input\s*\{[^}]*background:\s*#eef4ff/);
  assert.match(workspaceCss, /\.trajectory-kind\.assistant,[\s\S]*background:\s*#f3edff/);
  assert.match(workspaceCss, /\.trajectory-kind\.tool,\s*\.trajectory-kind\.code\s*\{[^}]*background:\s*#fff3e7/);
  assert.match(workspaceCss, /\.trajectory-kind\.code\s*\{[^}]*background:\s*#fff3e7/);
  assert.match(trajectorySource, /return isRunCodeEvent\(event\) \? "PTC"/);
  assert.match(trajectorySource, /isPythonCodeEvent\(event\)\) tabs\.push\(\["code", "Code"\]\)/);
  assert.match(trajectorySource, /class="hljs language-python"/);
  assert.match(trajectorySource, /data-copy-run-code/);
  assert.match(trajectorySource, /activeTab === "code"[\s\S]*?"trajectory-detail-body is-code"/);
  assert.match(workspaceCss, /\.trajectory-detail-body\.is-code\s*\{[^}]*padding-top:\s*0;[^}]*padding-bottom:\s*0;[^}]*overflow:\s*hidden;/);
  assert.match(workspaceCss, /\.trajectory-code-view\s*\{[^}]*min-height:\s*100%;[^}]*display:\s*flex;[^}]*border:\s*0;[^}]*background:\s*transparent;/);
  assert.match(workspaceCss, /\.trajectory-code-view pre\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow:\s*auto;[^}]*background:\s*transparent;/);
  assert.match(workspaceCss, /\.trajectory-code-view code\.hljs\s*\{[^}]*min-height:\s*100%;[^}]*padding:\s*0;[^}]*background:\s*transparent;/);
  assert.doesNotMatch(trajectorySource, /\bturns?\b/i);
  assert.match(trajectorySource, /currentUser\.run_id = currentRunId/);
  assert.doesNotMatch(trajectorySource, /Run \$\{groupOrdinal\}|data-trajectory-group/);
  assert.doesNotMatch(workspaceCss, /@media[^}]+trajectory-layout[^}]+grid-template-columns:\s*1fr/s);
});

test("公共JSON视图区分键值类型并安全转义", async () => {
  const { runInNewContext } = await import("node:vm");
  const context = { window: {} };
  runInNewContext(jsonViewSource, context);
  const html = context.window.DeepAgentJson.renderBlock({
    title: "<script>alert(1)</script>",
    count: 12,
    enabled: true,
    empty: null,
  });
  assert.match(html, /class="json-view"/);
  assert.match(html, /class="json-key"/);
  assert.match(html, /class="json-string"/);
  assert.match(html, /class="json-number"/);
  assert.match(html, /class="json-boolean"/);
  assert.match(html, /class="json-null"/);
  assert.doesNotMatch(html, /<script>/);
});

test("上下文的外层折叠可直接展开JSON，其他JSON保留默认折叠规则", async () => {
  const { runInNewContext } = await import("node:vm");
  const calls = [];
  const context = { window: { DeepAgentMarkdown: { renderCodeBlock: (_code, _lang, options) => {
    calls.push(options); return "<pre>JSON</pre>";
  } } } };
  runInNewContext(jsonViewSource, context);
  context.window.DeepAgentJson.renderBlock({ value: 0 }, { expanded: true });
  context.window.DeepAgentJson.renderBlock({ value: false });
  assert.equal(calls[0].expanded, true);
  assert.equal(calls[1].expanded, undefined);
});

test("轨迹JSON自动换行且只由详情正文承担滚动", () => {
  assert.match(consoleCss, /\.json-view\s*\{[\s\S]*?overflow-x:\s*hidden;[\s\S]*?white-space:\s*pre-wrap;/);
  assert.match(consoleCss, /\.json-view code\s*\{[^}]*min-width:\s*0;[^}]*white-space:\s*inherit;/);
  assert.match(workspaceCss, /\.trajectory-detail-body \.trajectory-json\s*\{[\s\S]*?max-height:\s*none;[\s\S]*?overflow:\s*visible;/);
});

test("Console正文不低于12px，只有明确的辅助标签使用紧凑字号", () => {
  const lowFontDeclarations = (css) => {
    const sizes = [];
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    for (const rule of withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rule[1].trim();
      for (const match of rule[2].matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/gi)) {
        if (Number(match[1]) < 12) sizes.push([selector, match[0]]);
      }
      for (const match of rule[2].matchAll(/(?<!-)font\s*:\s*([^;{}]+)/gi)) {
        for (const size of match[1].matchAll(/(\d+(?:\.\d+)?)px/g)) {
          if (Number(size[1]) < 12) sizes.push([selector, match[0]]);
        }
      }
    }
    return sizes;
  };
  assert.deepEqual(lowFontDeclarations(workspaceCss), [[".trajectory-kind", "font-size: 10px"]]);
  assert.deepEqual(lowFontDeclarations(consoleCss), [
    [".console-rail .resource-group-label", "font-size: 11px"],
    [".console-brand .console-brand-copy .connection-pill", "font-size: 10px"],
    [".agent-name-line .agent-channel-badge", "font-size: 11px"],
    ["#workspaceLegacyHost .session-list-footer", "font-size: 11px"],
    [".inspector-facts .inspector-channel-badge", "font-size: 11px"],
    [".catalog-model-table .model-tag-cell .badge", "font-size: 11px"],
  ]);
  assert.deepEqual(lowFontDeclarations(configurationCss), [[".provider-address-mode", "font-size: 11px"]]);
  assert.deepEqual(lowFontDeclarations(themesCss), []);
  assert.match(consoleCss, /\.agent-select-label[^}]*font-size:\s*14px/);
});


test("前端工具回传后助手 Pending 解除，部分返回与无关联返回不解除", () => {
  const call = (id, run, ids, sequence, turn_id = "turn-1") => ({ event_id: id, event_type: "assistant/message", status: "pending", sequence, run_id: run, turn_id,
    payload: { message: { kind: "response", parts: ids.map(tool_call_id => ({ part_kind: "tool-call", tool_name: "builtin__choice", tool_call_id, args: {}, metadata: { widget: "builtin.choice" } })) } } });
  const result = (id, callId, sequence) => ({ event_id: id, event_type: "tool/results", sequence, run_id: "resumed-run", turn_id: "turn-1",
    payload: { message: { kind: "request", parts: [{ part_kind: "tool-return", tool_name: "builtin__choice", tool_call_id: callId, content: "chosen" }] } } });
  const first = call("pending-first", "run-1", ["call-a", "call-b"], 1);
  assert.equal(buildTrajectory({ items: [first, result("result-a", "call-a", 2)] })[0].status, "pending");
  const completed = buildTrajectory({ items: [first, result("result-a", "call-a", 2), result("result-b", "call-b", 3)] });
  assert.equal(completed[0].status, "completed");
  assert.equal(first.status, "pending", "原始持久记录不改写");
  const reused = buildTrajectory({ items: [call("old", "run-old", ["reused"], 1, "old-turn"), call("new", "run-new", ["reused"], 2), result("later", "reused", 3)] });
  assert.equal(reused.find(e => e.id === "old").status, "pending");
  assert.equal(reused.find(e => e.id === "new").status, "completed");
});
