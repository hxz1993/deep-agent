import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildTrajectory,
  filterTrajectory,
  redactTrajectoryValue,
  runCodeSource,
  shouldIngestTrajectoryEvent,
  trajectoryStats,
} from "../../../frontend/console/trajectory.js";

const template = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);
const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);
const configurationCss = await readFile(
  new URL("../../../frontend/console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const themesCss = await readFile(
  new URL("../../../frontend/console/themes.css", import.meta.url),
  "utf8",
);
const trajectorySource = await readFile(
  new URL("../../../frontend/console/trajectory.js", import.meta.url),
  "utf8",
);
const jsonViewSource = await readFile(
  new URL("../../../frontend/console/json-view.js", import.meta.url),
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
  assert.match(chatSource, /trajectory\?\.setHistory\(eventResult\.response\.ok/);
  assert.match(chatSource, /trajectory\?\.ingest\(\{ event: type, data \}\)/);
  assert.match(workspaceCss, /\.chat-view-tabs[\s\S]*border-radius:\s*8px/);
  assert.doesNotMatch(workspaceCss, /\.chat-view-tab\.active::after/);
  assert.match(workspaceCss, /\.trajectory-layout[\s\S]*display:\s*block/);
  assert.doesNotMatch(workspaceCss, /trajectory-timeline|trajectory-segment|trajectory-sequence/);
  assert.match(workspaceCss, /\.inspector-pane\.trajectory-inspector/);
  assert.match(workspaceCss, /\.trajectory-fact[^}]*grid-template-columns:\s*64px minmax\(0, 1fr\)/);
  assert.match(trajectorySource, /kind === "input"\) return "USER"/);
  assert.match(trajectorySource, /kind === "context"\) return "CONTEXT"/);
  assert.match(trajectorySource, /DeepAgentMarkdown\?\.renderBody/);
  assert.match(trajectorySource, /DeepAgentJson\?\.renderBlock/);
  assert.match(workspaceCss, /\.trajectory-summary-markdown/);
  assert.match(workspaceCss, /\.trajectory-thinking-content[^}]*max-height:\s*50vh/);
  assert.match(trajectorySource, /const preview = `\$\{renderThinking\(event\.payload\?\.thinking\)\}\$\{renderMarkdown/);
  assert.match(workspaceCss, /\.trajectory-summary-preview \.trajectory-thinking,[\s\S]*border-left:\s*2px solid var\(--accent-soft\)/);
  assert.doesNotMatch(workspaceCss, /trajectory-thinking-preview|trajectory-technical-details/);
  assert.doesNotMatch(trajectorySource, /标识与模型信息|renderTechnicalDetails/);
  assert.match(trajectorySource, /\["summary", "Summary"\]/);
  assert.match(trajectorySource, /event\.kind === "title"\) return \[\["summary", "Summary"\], \["request", "Request"\], \["result", "Result"\]\]/);
  assert.match(trajectorySource, /tabs\.push\(\["preview", "Preview"\], \["raw", "Raw"\]\)/);
  assert.match(trajectorySource, /\["Thinking", event\.payload\?\.thinking\][\s\S]*\["Content", event\.payload\?\.content\]/);
  assert.match(trajectorySource, /completed: "Completed"/);
  assert.doesNotMatch(trajectorySource, /completed: "完成"|trajectoryRunCount/);
  assert.match(trajectorySource, /renderSummarySection\("Payload"[\s\S]*renderSummarySection\("Result"/);
  assert.match(trajectorySource, /data-trajectory-detail-tab-target/);
  assert.match(trajectorySource, /data-trajectory-event-target/);
  assert.match(trajectorySource, /renderDetail\(selected, detailTab, events\)/);
  assert.match(trajectorySource, /renderFact\("名称", reference\.label/);
  assert.match(trajectorySource, /renderFact\("类型", reference\.type/);
  assert.match(trajectorySource, /renderFact\("Key", reference\.key/);
  assert.match(trajectorySource, /renderFact\("版本", reference\.schema_version/);
  assert.match(trajectorySource, /renderSummarySection\("Attachments"/);
  assert.match(trajectorySource, /renderSummarySection\("Context References"/);
  assert.doesNotMatch(trajectorySource, /Object\.entries\(reference\.payload/);
  assert.match(trajectorySource, /TOOL_KINDS\.has\(event\.kind\) && hasMeaningfulValue\(event\.result\)/);
  assert.doesNotMatch(trajectorySource, /下载本地文件/);
  assert.match(trajectorySource, /trajectory-summary-link local-file-download/);
  assert.doesNotMatch(trajectorySource, /icon\("list-checks"\)|icon\("archive-restore"\)|icon\("message-square-more"\)/);
  assert.match(trajectorySource, /fileId\.startsWith\("local_"\)/);
  assert.match(chatSource, /renderLocalFileDownload\(data\.result, data\.artifacts\)/);
  assert.match(workspaceCss, /\.local-file-download\s*\{/);
  assert.match(chatSource, /data-copy-file-id/);
  assert.match(trajectorySource, /renderGeneratedFiles\(artifacts\)/);
  assert.match(trajectorySource, /metadata\.generated_files/);
  assert.match(workspaceCss, /\.generated-file-list\s*\{/);
  assert.doesNotMatch(trajectorySource, /\["timing", "Timing"\]/);
  assert.doesNotMatch(trajectorySource, /renderFact\("Role"|trajectory-preview-message|eventRole/);
  assert.doesNotMatch(trajectorySource, /trajectoryTimelineLayout|renderSequence|renderRowSignals|iconForKind/);
  assert.doesNotMatch(trajectorySource, /含思考|请求调用|个调用/);
  assert.match(workspaceCss, /\.trajectory-list\s*\{[\s\S]*overflow-x:\s*hidden/);
  assert.match(workspaceCss, /\.trajectory-summary-preview\.is-scroll \.trajectory-summary-section-body\s*\{[\s\S]*max-height:\s*180px;[\s\S]*overflow-y:\s*auto/);
  assert.doesNotMatch(workspaceCss, /\.trajectory-summary-preview\.is-scroll \.trajectory-summary-section-body\s*\{[^}]*\n\s*height:\s*180px/);
  assert.match(workspaceCss, /\.trajectory-export\s*\{[\s\S]*height:\s*32px;[\s\S]*min-height:\s*32px/);
  assert.match(workspaceCss, /\.trajectory-detail-body \.trajectory-json\.is-flat\s*\{[\s\S]*border:\s*0;[\s\S]*background:\s*transparent/);
  assert.match(workspaceCss, /\.trajectory-kind\.input\s*\{[^}]*background:\s*#eef4ff/);
  assert.match(workspaceCss, /\.trajectory-kind\.context\s*\{[^}]*background:\s*#e9f8f0/);
  assert.match(workspaceCss, /\.trajectory-kind\.assistant,[\s\S]*background:\s*#f3edff/);
  assert.match(workspaceCss, /\.trajectory-kind\.tool\s*\{[^}]*background:\s*#fff3e7/);
  assert.match(workspaceCss, /\.trajectory-kind\.code\s*\{[^}]*background:\s*#e8f1ff/);
  assert.match(trajectorySource, /return isRunCodeEvent\(event\) \? "PTC"/);
  assert.match(trajectorySource, /isRunCodeEvent\(event\)\) tabs\.push\(\["code", "Code"\]\)/);
  assert.match(trajectorySource, /class="hljs language-python"/);
  assert.match(trajectorySource, /data-copy-run-code/);
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

test("轨迹JSON自动换行且只由详情正文承担滚动", () => {
  assert.match(consoleCss, /\.json-view\s*\{[\s\S]*?overflow-x:\s*hidden;[\s\S]*?white-space:\s*pre-wrap;/);
  assert.match(consoleCss, /\.json-view code\s*\{[^}]*min-width:\s*0;[^}]*white-space:\s*inherit;/);
  assert.match(workspaceCss, /\.trajectory-detail-body \.trajectory-json\s*\{[\s\S]*?max-height:\s*none;[\s\S]*?overflow:\s*visible;/);
});

test("Console自有样式不再使用低于12px的可读文字", () => {
  const lowFontDeclarations = (css) => {
    const sizes = [];
    for (const match of css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/gi)) {
      if (Number(match[1]) < 12) sizes.push(match[0]);
    }
    for (const match of css.matchAll(/(?<!-)font\s*:\s*([^;{}]+)/gi)) {
      for (const size of match[1].matchAll(/(\d+(?:\.\d+)?)px/g)) {
        if (Number(size[1]) < 12) sizes.push(match[0]);
      }
    }
    return sizes;
  };
  assert.deepEqual(lowFontDeclarations(workspaceCss), []);
  assert.deepEqual(lowFontDeclarations(consoleCss), []);
  assert.deepEqual(lowFontDeclarations(configurationCss), []);
  assert.deepEqual(lowFontDeclarations(themesCss), []);
  assert.match(consoleCss, /\.agent-select-label[^}]*font-size:\s*14px/);
});
