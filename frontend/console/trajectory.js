import { copyTextToClipboard } from "./message-actions.js?v=20260819-1";
import { parseServerTimestamp } from "./time.js?v=20260831-1";
import { renderWebImagePreview } from "./web-image.js?v=20260901-1";

const TOOL_KINDS = new Set(["tool", "planning", "deferred"]);
const PRESENTATION_ONLY_SSE_TYPES = new Set(["session_title"]);
const SENSITIVE_KEY = /(^|[_-])(authorization|cookie|set-cookie|token|api[_-]?key|password|secret)([_-]|$)/i;

function jsonClone(value) {
  if (value == null) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return String(value);
  }
}

export function redactTrajectoryValue(value) {
  if (Array.isArray(value)) return value.map(redactTrajectoryValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, child]) => [
    key,
    SENSITIVE_KEY.test(key) ? "[已隐藏]" : redactTrajectoryValue(child),
  ]));
}

export function shouldIngestTrajectoryEvent(type) {
  return !PRESENTATION_ONLY_SSE_TYPES.has(String(type || ""));
}

function normalizedStatus(status, fallback = "completed") {
  const value = String(status || fallback).toLowerCase();
  if (["failed", "error"].includes(value)) return "failed";
  if (["interrupted", "cancelled", "canceled"].includes(value)) return "interrupted";
  if (["pending", "running"].includes(value)) return value;
  return "completed";
}

function valuePreview(value, limit = 180) {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}…` : normalized;
}

function usageTotal(usage) {
  if (!usage || typeof usage !== "object") return null;
  const direct = Number(usage.total_tokens);
  if (Number.isFinite(direct)) return direct;
  const input = Number(usage.input_tokens);
  const output = Number(usage.output_tokens);
  if (Number.isFinite(input) || Number.isFinite(output)) {
    return (Number.isFinite(input) ? input : 0) + (Number.isFinite(output) ? output : 0);
  }
  return null;
}

function eventLabel(kind, part = {}) {
  if (kind === "input") return "用户输入";
  if (kind === "context") return "输入上下文";
  if (kind === "thinking") return "模型思考";
  if (kind === "assistant") return "助手输出";
  if (kind === "title") return "会话标题";
  if (kind === "run") return "模型运行";
  if (kind === "compaction") return "上下文压缩";
  if (kind === "planning") return part.tool || "任务规划";
  if (kind === "deferred") return part.tool || "等待用户操作";
  if (kind === "tool") return part.tool || "工具调用";
  return part.event_type || "会话事件";
}

function eventSummary(kind, source = {}) {
  if (kind === "input") return valuePreview(source.content) || "仅包含附件或上下文引用";
  if (kind === "context") return valuePreview(source.label || source.content || source.type || source.key) || "结构化上下文";
  if (["thinking", "assistant"].includes(kind)) return valuePreview(source.content);
  if (kind === "title") return valuePreview(source.title) || "会话标题";
  if (kind === "run") return source.status ? `Run ${source.status}` : "模型运行";
  if (kind === "compaction") {
    const before = source.budget?.estimated_before;
    const after = source.budget?.estimated_after;
    return before != null && after != null ? `${before} → ${after} Token` : "上下文压缩事件";
  }
  if (source.type === "tool_result") return valuePreview(source.result ?? source.content) || "工具已返回";
  return valuePreview(source.args) || "等待参数或结果";
}

function newEvent({
  id,
  kind,
  inputSequence,
  runId = null,
  timestamp = null,
  status = "completed",
  part = {},
  message = {},
  payload = null,
  result = null,
  native = null,
  source = "history",
}) {
  return {
    id,
    kind,
    input_sequence: inputSequence,
    run_id: runId,
    timestamp,
    status: normalizedStatus(status),
    label: eventLabel(kind, part),
    summary: eventSummary(
      kind,
      part && typeof part === "object" && Object.keys(part).length ? part : message,
    ),
    tool_call_id: part.tool_call_id || null,
    source: part.source || source,
    payload: redactTrajectoryValue(jsonClone(payload)),
    result: redactTrajectoryValue(jsonClone(result)),
    native: redactTrajectoryValue(jsonClone(native)),
  };
}

function partKind(part, toolName) {
  if (toolName === "write_plan") return "planning";
  if (part?.type === "tool_call" && (part?.widget || part?.metadata?.widget)) return "deferred";
  return "tool";
}

function assistantLoopPayload(parts = []) {
  const thinking = parts
    .filter((part) => part?.type === "thinking")
    .map((part) => part.content || "")
    .join("") || null;
  const content = parts
    .filter((part) => part?.type === "text")
    .map((part) => part.content || "")
    .join("") || null;
  const toolCalls = parts
    .filter((part) => part?.type === "tool_call")
    .map((part) => ({
      tool: part.tool || null,
      tool_call_id: part.tool_call_id || null,
      args: part.args ?? null,
      source: part.source || null,
      widget: part.widget || null,
      metadata: part.metadata || null,
    }));
  return { thinking, content, tool_calls: toolCalls };
}

function assistantLoopSummary(payload = {}) {
  if (payload.content) return valuePreview(payload.content);
  return payload.thinking ? "模型处理中" : "模型响应";
}

function userPayload(content) {
  if (typeof content === "string") return { content, attachments: [], context_refs: [] };
  if (!Array.isArray(content)) return { content: null, attachments: [], context_refs: [] };
  const texts = [];
  const attachments = [];
  const contextRefs = [];
  content.forEach((item) => {
    if (typeof item === "string") texts.push(item);
    else if (item?.kind === "text-content") texts.push(String(item.content || ""));
    else if (item?.kind === "attachment-ref") attachments.push(item);
    else if (item?.kind === "context-ref") contextRefs.push(item);
  });
  return { content: texts.filter(Boolean).join("\n") || null, attachments, context_refs: contextRefs };
}

function storedMessage(event) {
  return event?.payload?.message && typeof event.payload.message === "object"
    ? event.payload.message
    : null;
}

function messageMeta(message = {}) {
  const { parts: _parts, ...meta } = message;
  return meta;
}

function toolRole(part) {
  if (["tool-call", "builtin-tool-call"].includes(part?.part_kind)) return "call";
  if (["tool-return", "builtin-tool-return", "retry-prompt"].includes(part?.part_kind)) return "result";
  return null;
}

function toolSource(toolName) {
  if (toolName === "write_plan") return "planning";
  if (["search_tools", "tool_search"].includes(toolName)) return "tool_search";
  return null;
}

function projectCompaction(event, inputSequence) {
  const projected = newEvent({
    id: event.event_id,
    kind: "compaction",
    inputSequence: Math.max(inputSequence, 1),
    runId: event.run_id || null,
    timestamp: event.created_at || null,
    status: event.status || "running",
    part: event.payload || {},
    payload: event.payload || {},
  });
  projected.sequence = Number(event.sequence || 0);
  projected.turn_id = event.turn_id || null;
  projected.event_type = event.event_type;
  projected.timestamp_end = event.updated_at || projected.timestamp;
  const durationMs = Number(event.payload?.duration_ms);
  if (Number.isFinite(durationMs)) projected.duration_ms = durationMs;
  return projected;
}

function projectTitle(event, inputSequence) {
  const payload = event.payload || {};
  const projected = newEvent({
    id: event.event_id,
    kind: "title",
    inputSequence: Math.max(inputSequence, 1),
    runId: event.run_id || null,
    timestamp: event.created_at || null,
    status: event.status || "completed",
    part: payload,
    payload,
    native: {
      event_ids: [event.event_id],
      title_request: payload,
      title_result: payload,
    },
  });
  projected.sequence = Number(event.sequence || 0);
  projected.turn_id = event.turn_id || null;
  projected.event_type = event.event_type;
  projected.timestamp_end = event.updated_at || projected.timestamp;
  return projected;
}

/** Build semantic trajectory rows from complete Message Events. */
export function buildTrajectory(history = {}) {
  const items = [...(Array.isArray(history?.items) ? history.items : [])]
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
  let inputSequence = 0;
  const projected = [];
  const tools = new Map();
  const automaticTitles = new Map();
  for (const event of items) {
    if (event.event_type === "session/title") {
      const source = event.payload?.source;
      const sourceEventId = event.payload?.source_event_id;
      const automaticKey = source !== "user" && sourceEventId ? sourceEventId : null;
      const existing = automaticKey ? automaticTitles.get(automaticKey) : null;
      if (existing) {
        existing.status = normalizedStatus(event.status || "completed");
        existing.summary = eventSummary("title", event.payload || {});
        existing.payload = redactTrajectoryValue(jsonClone(event.payload || {}));
        existing.native.title_result = redactTrajectoryValue(jsonClone(event.payload || {}));
        existing.timestamp_end = event.updated_at || event.created_at || existing.timestamp_end;
        existing.native.event_ids.push(event.event_id);
      } else {
        const title = projectTitle(event, inputSequence);
        projected.push(title);
        if (automaticKey) automaticTitles.set(automaticKey, title);
      }
      continue;
    }
    if (event.event_type === "compaction") {
      projected.push(projectCompaction(event, inputSequence));
      continue;
    }
    if (event.event_type === "run/terminal") {
      const terminal = newEvent({
        id: event.event_id,
        kind: "run",
        inputSequence: Math.max(inputSequence, 1),
        runId: event.run_id || null,
        timestamp: event.created_at || null,
        status: event.status || "failed",
        part: { status: event.status },
        payload: event.payload || {},
      });
      terminal.sequence = Number(event.sequence || 0);
      terminal.turn_id = event.turn_id || null;
      terminal.event_type = event.event_type;
      projected.push(terminal);
      continue;
    }
    const message = storedMessage(event);
    if (!message) continue;
    const parts = Array.isArray(message.parts) ? message.parts : [];
    if (event.event_type === "user/message") {
      inputSequence += 1;
      const prompt = parts.find((part) => part?.part_kind === "user-prompt");
      const payload = userPayload(prompt?.content);
      payload.instructions = message.instructions || null;
      payload.system_parts = parts.filter((part) => ["system-prompt", "instruction"].includes(part?.part_kind));
      const input = newEvent({
        id: event.event_id,
        kind: "input",
        inputSequence,
        runId: event.run_id || message.run_id || null,
        timestamp: event.created_at || prompt?.timestamp || message.timestamp || null,
        part: { content: payload.content },
        payload,
        native: messageMeta(message),
      });
      input.sequence = Number(event.sequence || 0);
      input.turn_id = event.turn_id || null;
      input.event_type = event.event_type;
      input.timestamp_end = event.updated_at || input.timestamp;
      projected.push(input);
    }

    if (event.event_type === "assistant/message") {
      const assistantParts = parts.map((part) => {
        if (part?.part_kind === "thinking") return { type: "thinking", content: part.content || "" };
        if (part?.part_kind === "text") return { type: "text", content: part.content || "" };
        if (toolRole(part) === "call") return {
          type: "tool_call",
          tool: part.tool_name || null,
          tool_call_id: part.tool_call_id || null,
          args: part.args ?? null,
          source: toolSource(part.tool_name),
          widget: part.metadata?.widget || null,
          metadata: part.metadata || null,
        };
        return null;
      }).filter(Boolean);
      const assistantPayload = assistantLoopPayload(assistantParts);
      if (assistantPayload.thinking || assistantPayload.content) {
        const assistant = newEvent({
          id: event.event_id,
          kind: "assistant",
          inputSequence: Math.max(inputSequence, 1),
          runId: event.run_id || message.run_id || null,
          timestamp: event.created_at || message.timestamp || null,
          status: event.status || message.state || "completed",
          part: { content: assistantPayload.content },
          payload: assistantPayload,
          native: messageMeta(message),
        });
        assistant.summary = assistantLoopSummary(assistantPayload);
        assistant.sequence = Number(event.sequence || 0);
        assistant.turn_id = event.turn_id || null;
        assistant.event_type = event.event_type;
        assistant.timestamp_end = event.updated_at || assistant.timestamp;
        projected.push(assistant);
      }
    }

    parts.forEach((part, offset) => {
      const role = toolRole(part);
      const callId = part?.tool_call_id;
      if (!role || !callId) return;
      const toolName = part.tool_name || null;
      let tool = tools.get(callId);
      if (!tool) {
        const pseudoPart = {
          type: role === "call" ? "tool_call" : "tool_result",
          tool: toolName,
          tool_call_id: callId,
          args: part.args ?? null,
          result: part.content ?? null,
          source: toolSource(toolName),
          widget: part.metadata?.widget || null,
          metadata: part.metadata || null,
        };
        tool = newEvent({
          id: `tool:${callId}`,
          kind: partKind(pseudoPart, toolName),
          inputSequence: Math.max(inputSequence, 1),
          runId: event.run_id || message.run_id || null,
          timestamp: event.created_at || part.timestamp || null,
          status: role === "call" ? (event.status === "interrupted" ? "interrupted" : "pending") : "completed",
          part: pseudoPart,
          payload: role === "call" ? { args: part.args ?? null, metadata: part.metadata || null } : {},
          result: role === "result" ? { result: part.content ?? null, outcome: part.outcome || null } : null,
          native: { call_event_id: role === "call" ? event.event_id : null, result_event_id: role === "result" ? event.event_id : null },
        });
        tool.sequence = Number(event.sequence || 0) + (offset + 1) / 1000;
        tool.turn_id = event.turn_id || null;
        tool.event_type = "tool/interaction";
        tool.timestamp_end = event.updated_at || tool.timestamp;
        tools.set(callId, tool);
        projected.push(tool);
      }
      if (role === "call") {
        tool.kind = partKind({ type: "tool_call", widget: part.metadata?.widget, metadata: part.metadata }, toolName);
        tool.label = eventLabel(tool.kind, { tool: toolName });
        tool.payload = redactTrajectoryValue(jsonClone({ args: part.args ?? null, metadata: part.metadata || null }));
        tool.native = { ...(tool.native || {}), call_event_id: event.event_id, call_part_offset: offset };
      } else {
        const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
        tool.status = normalizedStatus(part.outcome === "failed" ? "failed" : "completed");
        tool.result = redactTrajectoryValue(jsonClone({
          result: part.content ?? null,
          outcome: part.outcome || null,
          plan: toolName === "write_plan" ? metadata.deepagent_plan || null : null,
        }));
        tool.native = { ...(tool.native || {}), result_event_id: event.event_id, result_part_offset: offset };
        tool.timestamp_end = event.updated_at || part.timestamp || tool.timestamp_end;
        tool.summary = eventSummary(tool.kind, { type: "tool_result", result: part.content });
      }
    });
  }
  return projected.sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
}

export function trajectoryStats(events = []) {
  return {
    inputs: new Set(events.map((event) => event.input_sequence).filter(Boolean)).size,
    calls: events.filter((event) => (
      TOOL_KINDS.has(event.kind)
      && event.event_type === "tool/interaction"
    )).length,
    runs: new Set(events.map((event) => event.run_id).filter(Boolean)).size,
  };
}

export function filterTrajectory(events = [], { mode = "sequence", query = "" } = {}) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  return events.filter((event) => {
    if (mode === "calls" && !(
      TOOL_KINDS.has(event.kind)
      && event.event_type === "tool/interaction"
    )) return false;
    if (!needle) return true;
    const haystack = [
      event.label,
      event.summary,
      event.run_id,
      event.tool_call_id,
      JSON.stringify(event.payload),
      JSON.stringify(event.result),
    ].join(" ").toLocaleLowerCase();
    return haystack.includes(needle);
  });
}

function liveEventKind(type, data) {
  if (["thinking", "text"].includes(type)) return "assistant";
  if (["cancelled", "error"].includes(type)) return "run";
  if (["context_compaction", "context_compaction_progress", "context_compaction_result"].includes(type)) return "compaction";
  if (type === "tool_call" || type === "tool_result" || type === "tool_call_removed") {
    if (data?.source === "planning") return "planning";
    if (data?.widget) return "deferred";
    return "tool";
  }
  return type;
}

function liveLabel(type, data) {
  if (type === "session") return "Run 已建立";
  if (type === "final") return "Run 终态";
  if (type === "cancelled") return "Run 已取消";
  if (type === "error") return "Run 错误";
  if (type.startsWith("context_compaction")) return "上下文压缩";
  return eventLabel(liveEventKind(type, data), data);
}

function liveSummary(type, data) {
  if (["thinking", "text"].includes(type)) return valuePreview(data?.delta);
  if (type === "session") return data?.run_id ? `run_id=${data.run_id}` : "会话已建立";
  if (type === "final") return data?.status || "completed";
  if (type === "cancelled") return "cancelled";
  if (type === "error") return valuePreview(data?.message) || "请求失败";
  if (type === "tool_result") return valuePreview(data?.result) || "工具已返回";
  if (type === "tool_call_removed") return data?.reason || "调用已撤回";
  return valuePreview(data?.message || data?.args) || type;
}

function formatJson(value) {
  if (value == null) return "暂无";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatTime(value) {
  if (!value) return "—";
  const date = parseServerTimestamp(value);
  if (!date) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hour12: false,
  }).format(date);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;");
}

function kindBadge(kind) {
  if (kind === "input") return "USER";
  if (kind === "context") return "CONTEXT";
  if (kind === "title") return "TITLE";
  return String(kind || "event").toUpperCase();
}

function isRunCodeEvent(event) {
  return event?.kind === "tool" && event?.label === "run_code";
}

function eventKindClass(event) {
  return isRunCodeEvent(event) ? "code" : event?.kind;
}

function eventKindBadge(event) {
  return isRunCodeEvent(event) ? "CODE" : kindBadge(event?.kind);
}

function icon(name) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

function statusLabel(status) {
  return {
    completed: "Completed",
    running: "Running",
    pending: "Pending",
    interrupted: "Interrupted",
    failed: "Failed",
  }[status] || status;
}

function renderFact(label, value, { mono = false } = {}) {
  if (value == null || value === "") return "";
  return `<div class="trajectory-fact"><span>${escapeHtml(label)}</span><strong${mono ? ' class="mono"' : ""}>${escapeHtml(value)}</strong></div>`;
}

function hasMeaningfulValue(value) {
  if (value == null || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function eventDuration(event) {
  if (Number.isFinite(Number(event.duration_ms))) return Number(event.duration_ms);
  const started = event.timestamp ? parseServerTimestamp(event.timestamp)?.getTime() : NaN;
  const ended = event.timestamp_end ? parseServerTimestamp(event.timestamp_end)?.getTime() : NaN;
  return Number.isFinite(started) && Number.isFinite(ended) ? Math.max(0, ended - started) : null;
}

function durationLabel(value) {
  if (!Number.isFinite(value)) return null;
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

function renderMarkdown(content, emptyMessage = "当前事件没有正文。") {
  if (!content) return `<p class="trajectory-summary-empty">${escapeHtml(emptyMessage)}</p>`;
  const rendered = globalThis.DeepAgentMarkdown?.renderBody?.(content)
    || `<div class="markdown-body"><p>${escapeHtml(content)}</p></div>`;
  return `<div class="trajectory-summary-markdown">${rendered}</div>`;
}

function renderMeta(event) {
  const total = usageTotal(event.native?.usage);
  const duration = eventDuration(event);
  const items = [
    ["clock-3", event.timestamp ? formatTime(event.timestamp) : null],
    ["timer", durationLabel(duration)],
    ["coins", total == null ? null : `${total.toLocaleString("zh-CN")} Token`],
  ].filter(([, value]) => value);
  if (!items.length) return "";
  return `<div class="trajectory-summary-meta">${items.map(([name, value]) => `<span>${icon(name)}${escapeHtml(value)}</span>`).join("")}</div>`;
}

function renderSummarySection(title, body, { targetTab = null, className = "" } = {}) {
  if (!body) return "";
  const heading = targetTab
    ? `<button type="button" data-trajectory-detail-tab-target="${escapeHtml(targetTab)}"><span>${escapeHtml(title)}</span>${icon("chevron-right")}</button>`
    : `<div><span>${escapeHtml(title)}</span></div>`;
  return `<section class="trajectory-summary-section ${escapeHtml(className)}">${heading}<div class="trajectory-summary-section-body">${body}</div></section>`;
}

function renderJumpLink({ eventId, iconName, text, detail = "" }) {
  const attributes = eventId ? ` data-trajectory-event-target="${escapeHtml(eventId)}"` : " disabled";
  return `<button type="button" class="trajectory-summary-link"${attributes}>${icon(iconName)}<span><strong>${escapeHtml(text)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</span>${eventId ? icon("arrow-up-right") : ""}</button>`;
}

function renderInputSummary(event, events) {
  const contextRefs = Array.isArray(event.payload?.context_refs) ? event.payload.context_refs : [];
  const attachments = Array.isArray(event.payload?.attachments) ? event.payload.attachments : [];
  const contextEvents = events.filter((item) => item.kind === "context" && item.payload?.parent_input_id === event.id);
  const contextLinks = contextRefs.map((reference, index) => renderJumpLink({
      eventId: contextEvents[index]?.id || null,
      iconName: "braces",
      text: reference?.label || reference?.key || reference?.type || "未命名上下文",
      detail: reference?.type || reference?.key || "",
    })).join("");
  const attachmentLinks = attachments.map((attachment) => renderJumpLink({
      eventId: null,
      iconName: "paperclip",
      text: attachment?.name || attachment?.id || "未命名附件",
      detail: attachment?.mime_type || attachment?.mime || "附件",
    })).join("");
  return `${renderSummarySection(
    "Preview",
    renderMarkdown(event.payload?.content, "本次输入只包含附件或结构化上下文。"),
    { targetTab: "preview", className: "trajectory-summary-preview is-scroll" },
  )}${attachmentLinks ? renderSummarySection("Attachments", `<div class="trajectory-summary-links">${attachmentLinks}</div>`) : ""}
    ${contextLinks ? renderSummarySection("Context References", `<div class="trajectory-summary-links">${contextLinks}</div>`) : ""}`;
}

function renderContextSummary(event) {
  const reference = event.payload?.reference || {};
  return renderSummarySection("Context", `<p class="trajectory-summary-lead">${escapeHtml(reference.label || reference.key || reference.type || "结构化上下文")}</p>
    <div class="trajectory-context-standard">
      ${renderFact("名称", reference.label || "未命名")}
      ${renderFact("类型", reference.type)}
      ${renderFact("Key", reference.key, { mono: true })}
      ${renderFact("版本", reference.schema_version)}
    </div>`);
}

function renderThinking(thinking) {
  if (!thinking) return "";
  return `<details class="trajectory-thinking">
    <summary><span>${icon("brain")}Thinking</span>${icon("chevron-down")}</summary>
    <div class="trajectory-thinking-content">${escapeHtml(thinking)}</div>
  </details>`;
}

function renderAssistantTools(event, events) {
  const calls = Array.isArray(event.payload?.tool_calls) ? event.payload.tool_calls : [];
  if (!calls.length) return "";
  return `<div class="trajectory-summary-links">${calls.map((call) => {
    const target = events.find((item) => TOOL_KINDS.has(item.kind) && item.tool_call_id && item.tool_call_id === call?.tool_call_id);
    return renderJumpLink({
      eventId: target?.id || null,
      iconName: "wrench",
      text: call?.tool || "未命名工具",
      detail: valuePreview(call?.args, 120),
    });
  }).join("")}</div>`;
}

function renderAssistantSummary(event, events) {
  const preview = `${renderThinking(event.payload?.thinking)}${renderMarkdown(
    event.payload?.content,
    event.payload?.thinking ? "模型尚未输出正文。" : "本次模型响应没有正文。",
  )}`;
  return `${renderSummarySection(
    "Preview",
    preview,
    { targetTab: "preview", className: "trajectory-summary-preview trajectory-summary-assistant-preview is-scroll" },
  )}
    ${event.payload?.tool_calls?.length ? renderSummarySection("Tools", renderAssistantTools(event, events)) : ""}`;
}

function toolResultValue(event) {
  return event.result && Object.prototype.hasOwnProperty.call(event.result, "result")
    ? event.result.result
    : event.result;
}

function localFileDownload(value) {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = localFileDownload(child);
      if (found) return found;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const fileId = String(value.file_id || "");
  if (fileId.startsWith("local_")) {
    return { fileId, fileName: value.file_name || fileId };
  }
  for (const child of Object.values(value)) {
    const found = localFileDownload(child);
    if (found) return found;
  }
  return null;
}

function renderLocalFileDownload(value) {
  const file = localFileDownload(value);
  if (!file) return "";
  const path = `api/v1/local-files/${encodeURIComponent(file.fileId)}/download`;
  const href = globalThis.serviceUrl?.(path) || `/${path}`;
  return renderSummarySection(
    "File",
    `<a class="trajectory-summary-link local-file-download" href="${escapeHtml(href)}" download>${icon("download")}<span><strong>${escapeHtml(file.fileName)}</strong></span></a>`,
  );
}

function renderPayloadResultSections({ payload, result }) {
  const payloadText = hasMeaningfulValue(payload) ? valuePreview(payload, 280) : null;
  const resultText = hasMeaningfulValue(result) ? valuePreview(result, 360) : null;
  return `${payloadText ? renderSummarySection("Payload", `<p class="trajectory-summary-code">${escapeHtml(payloadText)}</p>`, { targetTab: "payload" }) : ""}
    ${resultText ? renderSummarySection("Result", `<p class="trajectory-summary-code">${escapeHtml(resultText)}</p>`, { targetTab: "result" }) : ""}`;
}

function renderToolSummary(event) {
  const result = toolResultValue(event);
  const imagePreview = renderWebImagePreview(result, { className: "trajectory-web-image-preview" });
  return `${imagePreview ? renderSummarySection("Web Image", imagePreview) : ""}${renderPayloadResultSections({
    payload: event.payload?.args ?? event.payload,
    result,
  })}${renderLocalFileDownload(result)}`;
}

function planSnapshot(event) {
  return event.result?.plan || event.payload?.plan || event.result?.result?.plan || null;
}

function renderPlanningSummary(event) {
  const plan = planSnapshot(event);
  const completed = Number(plan?.completed || 0);
  const total = Number(plan?.total || plan?.items?.length || 0);
  const label = event.label === "write_plan" ? "创建或改写任务计划" : event.label;
  const progress = `<p class="trajectory-summary-lead"><strong>${escapeHtml(label)}</strong></p>
    ${total ? `<div class="trajectory-plan-progress"><span style="width:${Math.min(100, Math.max(0, completed / total * 100))}%"></span></div><p class="trajectory-plan-count">${completed} / ${total} 已完成</p>` : ""}`;
  return `${renderSummarySection("Plan", progress)}
    ${renderPayloadResultSections({ payload: event.payload, result: toolResultValue(event) })}`;
}

function renderDeferredSummary(event) {
  const args = event.payload?.args || {};
  const title = args.title || args.prompt || event.label;
  const reason = args.reason || args.message || event.summary;
  const primary = `<p class="trajectory-summary-lead"><strong>${escapeHtml(title)}</strong></p>
    ${reason && reason !== title ? `<p class="trajectory-summary-copy">${escapeHtml(reason)}</p>` : ""}`;
  return `${renderSummarySection("Interaction", primary)}
    ${renderPayloadResultSections({ payload: event.payload, result: toolResultValue(event) })}`;
}

function renderCompactionSummary(event) {
  const before = event.payload?.budget?.estimated_before;
  const after = event.payload?.budget?.estimated_after;
  const primary = before != null && after != null
    ? `${Number(before).toLocaleString("zh-CN")} → ${Number(after).toLocaleString("zh-CN")} Token`
    : event.summary;
  const source = event.payload?.source || {};
  const detail = [
    source.through_sequence != null ? `截至 Sequence ${source.through_sequence}` : null,
    event.payload?.summary_call?.model_id || null,
    event.payload?.error?.message || event.payload?.error || null,
  ].filter(Boolean).join(" · ");
  return renderSummarySection("Compaction", `<p class="trajectory-summary-lead"><strong>${escapeHtml(primary || event.label)}</strong></p>${detail ? `<p class="trajectory-summary-copy">${escapeHtml(detail)}</p>` : ""}`);
}

function renderTitleSummary(event) {
  const sourceLabel = {
    fallback: "确定性兜底",
    model: "模型生成",
    user: "用户修改",
  }[event.payload?.source] || event.payload?.source;
  const model = event.payload?.model_name || event.payload?.model_id;
  const facts = [
    renderFact("来源", sourceLabel),
    renderFact("模型", model, { mono: true }),
    renderFact("源事件", event.payload?.source_event_id, { mono: true }),
  ].join("");
  return renderSummarySection(
    "Title",
    `<p class="trajectory-summary-lead"><strong>${escapeHtml(event.payload?.title || event.summary || "会话标题")}</strong></p>${facts ? `<div class="trajectory-context-standard">${facts}</div>` : ""}`,
  );
}

const SUMMARY_RENDERERS = Object.freeze({
  input: renderInputSummary,
  context: renderContextSummary,
  assistant: renderAssistantSummary,
  planning: renderPlanningSummary,
  deferred: renderDeferredSummary,
  tool: renderToolSummary,
  compaction: renderCompactionSummary,
  title: renderTitleSummary,
});

function renderSummary(event, events) {
  const renderer = SUMMARY_RENDERERS[event.kind];
  const primary = renderer
    ? renderer(event, events)
    : renderSummarySection("Event", `<p class="trajectory-summary-lead">${escapeHtml(event.summary || event.label)}</p>`);
  return `<section class="trajectory-summary">${renderMeta(event)}<div class="trajectory-summary-primary">${primary}</div></section>`;
}

function renderJsonBlock(value) {
  return globalThis.DeepAgentJson?.renderBlock?.(value, {
    className: "trajectory-json is-flat",
    ariaLabel: "轨迹 JSON",
  }) || `<pre class="trajectory-json"><code>${escapeHtml(formatJson(value))}</code></pre>`;
}

export function runCodeSource(event) {
  const rawArgs = event?.payload?.args ?? event?.payload;
  if (rawArgs && typeof rawArgs === "object") {
    return typeof rawArgs.code === "string" ? rawArgs.code : "";
  }
  if (typeof rawArgs !== "string") return "";
  try {
    const parsed = JSON.parse(rawArgs);
    return parsed && typeof parsed === "object" && typeof parsed.code === "string"
      ? parsed.code
      : "";
  } catch {
    return "";
  }
}

function highlightPython(code) {
  if (globalThis.hljs?.getLanguage?.("python")) {
    try {
      return globalThis.hljs.highlight(code, { language: "python", ignoreIllegals: true }).value;
    } catch {
      // Fall through to escaped text when highlighting fails.
    }
  }
  return escapeHtml(code);
}

function renderRunCode(event) {
  const code = runCodeSource(event);
  if (!code) return `<p class="trajectory-summary-empty">本次代码编排没有可展示的源代码。</p>`;
  return `<section class="trajectory-code-view">
    <header><span>Python</span><button type="button" data-copy-run-code>${icon("copy")}复制</button></header>
    <pre><code class="hljs language-python">${highlightPython(code)}</code></pre>
  </section>`;
}

function detailTabs(event) {
  if (event.kind === "title") return [["summary", "Summary"], ["request", "Request"], ["result", "Result"]];
  const tabs = [["summary", "Summary"]];
  if (isRunCodeEvent(event)) tabs.push(["code", "Code"]);
  if (hasMeaningfulValue(event.payload)) tabs.push(["payload", "Payload"]);
  if (["input", "assistant"].includes(event.kind)) {
    tabs.push(["preview", "Preview"], ["raw", "Raw"]);
  }
  if (TOOL_KINDS.has(event.kind) && hasMeaningfulValue(event.result)) tabs.push(["result", "Result"]);
  return tabs;
}

function renderRaw(event) {
  if (event.kind === "assistant") {
    const sections = [
      ["Thinking", event.payload?.thinking],
      ["Content", event.payload?.content],
    ].filter(([, content]) => content);
    if (!sections.length) return `<p class="trajectory-summary-empty">暂无原始文本。</p>`;
    return `<div class="trajectory-raw-sections">${sections.map(([title, content]) => `
      <section class="trajectory-raw-section">
        <div class="trajectory-raw-heading">${escapeHtml(title)}</div>
        <pre>${escapeHtml(content)}</pre>
      </section>`).join("")}</div>`;
  }
  const content = event.kind === "input" ? event.payload?.content : formatJson(event.payload);
  return `<pre class="trajectory-raw">${escapeHtml(content || "暂无")}</pre>`;
}

function renderPreview(event) {
  if (event.kind === "assistant") {
    return `<section class="trajectory-preview">${renderThinking(event.payload?.thinking)}${renderMarkdown(event.payload?.content, "本次模型响应没有正文。")}</section>`;
  }
  return `<section class="trajectory-preview">${renderMarkdown(event.payload?.content, "本次输入没有文本正文。")}</section>`;
}

function renderDetail(event, activeTab = "summary", events = []) {
  if (!event) return `<div class="trajectory-detail-empty">${icon("scan-search")}<p>选择一条轨迹查看原生信息</p></div>`;
  const tabs = detailTabs(event);
  const allowedTabs = new Set(tabs.map(([id]) => id));
  if (!allowedTabs.has(activeTab)) activeTab = "summary";
  let content = renderSummary(event, events);
  if (activeTab === "request") content = renderJsonBlock(event.native?.title_request || event.payload);
  if (activeTab === "payload") content = renderJsonBlock(event.payload);
  if (activeTab === "result") content = renderJsonBlock(event.kind === "title" ? (event.native?.title_result || event.payload) : event.result);
  if (activeTab === "preview") content = renderPreview(event);
  if (activeTab === "raw") content = renderRaw(event);
  if (activeTab === "code") content = renderRunCode(event);
  const headerLabel = TOOL_KINDS.has(event.kind) || ["compaction", "title"].includes(event.kind)
    ? event.label
    : event.run_id ? `Run ${String(event.run_id).slice(0, 8)}` : "";
  return `
    <header class="trajectory-detail-head">
      <div><span class="trajectory-kind ${escapeHtml(eventKindClass(event))}">${escapeHtml(eventKindBadge(event))}</span>${headerLabel ? `<strong>${escapeHtml(headerLabel)}</strong>` : ""}<em class="${escapeHtml(event.status)}">${escapeHtml(statusLabel(event.status))}</em></div>
    </header>
    <nav class="trajectory-detail-tabs" role="tablist">${tabs.map(([id, label]) => `<button type="button" class="${activeTab === id ? "active" : ""}" data-trajectory-detail-tab="${id}">${label}</button>`).join("")}</nav>
    <div class="trajectory-detail-body">${content}</div>`;
}

export function createTrajectoryController({ state }) {
  const root = document.getElementById("trajectoryView");
  const list = document.getElementById("trajectoryList");
  const detail = document.getElementById("inspectorContent");
  const conversationTab = document.getElementById("conversationViewTab");
  const trajectoryTab = document.getElementById("trajectoryViewTab");
  const search = document.getElementById("trajectorySearch");
  const exportButton = document.getElementById("exportTrajectoryBtn");
  if (!root || !list || !detail || !conversationTab || !trajectoryTab) return null;

  let history = { items: [] };
  let historyEvents = [];
  let liveEvents = [];
  let currentRunId = null;
  let activeAssistantLoop = null;
  let assistantLoopSequence = 0;
  let selectedId = null;
  let detailTab = "summary";
  let mode = "sequence";
  let renderQueued = false;

  const allEvents = () => [...historyEvents, ...liveEvents].sort((left, right) => {
    const leftSequence = Number.isFinite(Number(left.sequence)) ? Number(left.sequence) : Number.MAX_SAFE_INTEGER;
    const rightSequence = Number.isFinite(Number(right.sequence)) ? Number(right.sequence) : Number.MAX_SAFE_INTEGER;
    return leftSequence - rightSequence;
  });

  function selectView(view) {
    const trajectoryActive = view === "trajectory";
    document.querySelector("main.chat")?.classList.toggle("trajectory-active", trajectoryActive);
    root.classList.toggle("hidden", !trajectoryActive);
    document.getElementById("messages")?.classList.toggle("hidden", trajectoryActive);
    document.querySelector("main.chat > .composer")?.classList.toggle("hidden", trajectoryActive);
    conversationTab.classList.toggle("active", !trajectoryActive);
    trajectoryTab.classList.toggle("active", trajectoryActive);
    conversationTab.setAttribute("aria-selected", String(!trajectoryActive));
    trajectoryTab.setAttribute("aria-selected", String(trajectoryActive));
    document.getElementById("inspectorPane")?.classList.toggle("trajectory-inspector", trajectoryActive);
    if (trajectoryActive) {
      window.DeepAgentConsoleWorkspace?.openInspectorPane?.();
      scheduleRender();
    } else {
      window.DeepAgentConsoleWorkspace?.renderInspector?.();
    }
  }

  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    window.requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }

  function renderRows(events) {
    if (!events.length) {
      list.innerHTML = `<div class="trajectory-empty">${icon("route")}<p>当前筛选下没有轨迹。</p></div>`;
      return;
    }
    const html = [];
    events.forEach((event) => {
      const showToolName = ["tool", "deferred"].includes(event.kind);
      const planningSummary = {
        write_plan: "创建或改写任务计划",
      }[event.label];
      const summary = event.kind === "planning" ? (planningSummary || "更新任务计划") : (event.summary || "暂无内容");
      html.push(`<button type="button" class="trajectory-row ${escapeHtml(event.kind)} ${selectedId === event.id ? "active" : ""}" data-trajectory-id="${escapeHtml(event.id)}">
        <span class="trajectory-row-copy"><span class="trajectory-kind ${escapeHtml(eventKindClass(event))}">${escapeHtml(eventKindBadge(event))}</span>${showToolName ? `<strong>${escapeHtml(event.label)}</strong>` : ""}<span class="trajectory-row-summary">${escapeHtml(summary)}</span></span>
        <span class="trajectory-row-meta"><time>${escapeHtml(formatTime(event.timestamp))}</time><em class="${escapeHtml(event.status)}">${escapeHtml(statusLabel(event.status))}</em></span>
      </button>`);
    });
    list.innerHTML = html.join("");
  }

  function render() {
    const events = allEvents();
    const stats = trajectoryStats(events);
    const visible = filterTrajectory(events, { mode, query: search?.value });
    document.getElementById("trajectoryCallCount").textContent = String(stats.calls);
    renderRows(visible);
    const selected = events.find((event) => event.id === selectedId) || visible[0] || null;
    selectedId = selected?.id || null;
    if (document.querySelector("main.chat")?.classList.contains("trajectory-active")) {
      detail.innerHTML = renderDetail(selected, detailTab, events);
      globalThis.DeepAgentMarkdown?.renderDiagrams?.(detail);
    }
    window.lucide?.createIcons();
  }

  function setHistory(nextHistory) {
    history = nextHistory || { items: [] };
    historyEvents = buildTrajectory(history);
    liveEvents = [];
    currentRunId = null;
    activeAssistantLoop = null;
    selectedId = historyEvents.at(-1)?.id || null;
    detailTab = "summary";
    scheduleRender();
  }

  function reset() {
    setHistory({ items: [] });
    selectView("conversation");
  }

  function addUser({ content, attachments = [], context_refs = [], branch_point = null }) {
    const inputSequence = trajectoryStats(allEvents()).inputs + 1;
    const inputEvent = newEvent({
      id: `live-user-${Date.now()}`,
      kind: "input",
      inputSequence,
      timestamp: new Date().toISOString(),
      source: "sse",
      message: { content },
      payload: { content: content || null, attachments, context_refs, branch_point },
    });
    liveEvents.push(inputEvent);
    context_refs.forEach((reference, referenceIndex) => {
      liveEvents.push(newEvent({
        id: `${inputEvent.id}-context-${referenceIndex}`,
        kind: "context",
        inputSequence,
        timestamp: inputEvent.timestamp,
        source: "sse",
        part: { type: "context", content: reference?.label || reference?.type || reference?.key },
        payload: { reference, parent_input_id: inputEvent.id },
      }));
    });
    scheduleRender();
  }

  function ensureLiveAssistant(inputSequence, timestamp) {
    if (activeAssistantLoop?.run_id === currentRunId && activeAssistantLoop.status === "running") {
      return activeAssistantLoop;
    }
    activeAssistantLoop = newEvent({
      id: `live-assistant-${currentRunId || "run"}-${assistantLoopSequence += 1}`,
      kind: "assistant",
      inputSequence,
      runId: currentRunId,
      timestamp,
      status: "running",
      part: { type: "assistant_loop", content: "" },
      payload: { thinking: null, content: null, tool_calls: [] },
      source: "sse",
    });
    liveEvents.push(activeAssistantLoop);
    return activeAssistantLoop;
  }

  function updateLiveAssistant(type, payload, inputSequence, timestamp) {
    const assistant = ensureLiveAssistant(inputSequence, timestamp);
    if (type === "thinking") {
      assistant.payload.thinking = `${assistant.payload.thinking || ""}${payload.delta || ""}` || null;
    } else if (type === "text") {
      assistant.payload.content = `${assistant.payload.content || ""}${payload.delta || ""}` || null;
    } else if (type === "tool_call") {
      const calls = assistant.payload.tool_calls;
      const existing = calls.find((item) => item.tool_call_id === payload.tool_call_id);
      const next = redactTrajectoryValue(jsonClone({
        tool: payload.tool || null,
        tool_call_id: payload.tool_call_id || null,
        args: payload.args ?? null,
        source: payload.source || null,
        widget: payload.widget || null,
        metadata: payload.metadata || null,
      }));
      if (existing) Object.assign(existing, next);
      else calls.push(next);
    }
    assistant.summary = assistantLoopSummary(assistant.payload);
    assistant.timestamp_end = timestamp;
    return assistant;
  }

  function ingest({ event, data }) {
    const type = event || data?.type;
    const payload = data || {};
    if (!type || !shouldIngestTrajectoryEvent(type)) return;
    if (["session_event_appended", "session_event_updated"].includes(type)) {
      const stored = payload.event || payload;
      if (!stored?.event_id || !stored?.event_type) return;
      const liveInput = stored.event_type === "user/message"
        ? [...liveEvents].reverse().find((item) => item.kind === "input")
        : null;
      const rawItems = Array.isArray(history.items) ? history.items : [];
      const rawIndex = rawItems.findIndex((item) => item.event_id === stored.event_id);
      if (rawIndex >= 0) rawItems[rawIndex] = stored;
      else rawItems.push(stored);
      history.items = rawItems.sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
      historyEvents = buildTrajectory(history);
      if (stored.event_type === "user/message") {
        if (liveInput) liveEvents = liveEvents.filter((item) => (
          item !== liveInput && item.payload?.parent_input_id !== liveInput.id
        ));
      }
      if (stored.event_type === "compaction") {
        liveEvents = liveEvents.filter((item) => !(item.kind === "compaction" && item.compaction_event_id === stored.event_id));
      }
      const storedParts = storedMessage(stored)?.parts || [];
      const storedCallIds = new Set(storedParts.map((part) => part?.tool_call_id).filter(Boolean));
      if (storedCallIds.size) {
        liveEvents = liveEvents.filter((item) => !storedCallIds.has(item.tool_call_id));
      }
      if (stored.event_type === "assistant/message" && stored.run_id) {
        liveEvents = liveEvents.filter((item) => item !== activeAssistantLoop || item.run_id !== stored.run_id);
        if (activeAssistantLoop?.run_id === stored.run_id) activeAssistantLoop = null;
      }
      const projectedTitle = stored.event_type === "session/title"
        ? historyEvents.find((item) => item.kind === "title" && item.native?.event_ids?.includes(stored.event_id))
        : null;
      selectedId = storedCallIds.size
        ? `tool:${[...storedCallIds][0]}`
        : projectedTitle?.id || stored.event_id;
      scheduleRender();
      return;
    }
    if (type === "context_compaction_progress") {
      const id = payload.event_id || "current";
      const existing = liveEvents.find((item) => item.kind === "compaction" && item.compaction_event_id === id);
      const next = {
        id: `live-compaction-${id}`,
        compaction_event_id: id,
        kind: "compaction",
        input_sequence: Math.max(trajectoryStats(allEvents()).inputs, 1),
        run_id: payload.run_id || currentRunId,
        timestamp: existing?.timestamp || new Date().toISOString(),
        timestamp_end: new Date().toISOString(),
        status: "running",
        label: "上下文压缩",
        summary: payload.phase || "正在处理上下文",
        tool_call_id: null,
        source: "sse",
        payload: redactTrajectoryValue(jsonClone(payload)),
        result: null,
        native: null,
      };
      if (existing) Object.assign(existing, next);
      else liveEvents.push(next);
      scheduleRender();
      return;
    }
        if (type === "session") {
      currentRunId = payload.run_id || currentRunId;
      const currentUser = [...liveEvents].reverse().find((item) => item.kind === "input" && !item.run_id);
      if (currentUser && currentRunId) {
        currentUser.run_id = currentRunId;
        liveEvents.filter((item) => (
          item.kind === "context"
          && !item.run_id
          && item.input_sequence === currentUser.input_sequence
        )).forEach((item) => { item.run_id = currentRunId; });
          }
          scheduleRender();
          return;
        }
    const inputSequence = Math.max(trajectoryStats(allEvents()).inputs, 1);
    const timestamp = new Date().toISOString();

    if (["thinking", "text"].includes(type)) {
      updateLiveAssistant(type, payload, inputSequence, timestamp);
      scheduleRender();
      return;
    }
        if (type === "tool_result" && payload.tool_call_id) {
      const call = [...liveEvents].reverse().find((item) => item.tool_call_id === payload.tool_call_id);
      if (call) {
        call.result = redactTrajectoryValue(jsonClone({ result: payload.result, artifacts: payload.artifacts || null, plan: payload.plan || null }));
        call.summary = liveSummary(type, payload);
        call.status = payload.outcome === "failed" ? "failed" : "completed";
        call.timestamp_end = timestamp;
        if (activeAssistantLoop?.run_id === currentRunId) {
          activeAssistantLoop.status = "completed";
          activeAssistantLoop = null;
        }
        scheduleRender();
        return;
      }
    }
    if (type === "tool_call_removed" && payload.tool_call_id) {
      const call = [...liveEvents].reverse().find((item) => item.tool_call_id === payload.tool_call_id);
      if (call) {
        call.status = "interrupted";
        call.summary = liveSummary(type, payload);
        call.timestamp_end = timestamp;
        if (activeAssistantLoop?.run_id === currentRunId) {
          activeAssistantLoop.status = "interrupted";
          activeAssistantLoop = null;
        }
        scheduleRender();
        return;
      }
    }
        if (type === "final") {
          liveEvents.filter((item) => item.run_id === currentRunId && item.status === "running")
            .forEach((item) => { item.status = "completed"; item.timestamp_end = timestamp; });
          activeAssistantLoop = null;
          scheduleRender();
          return;
    }
    if (["cancelled", "error"].includes(type)) {
      liveEvents.filter((item) => item.run_id === currentRunId && ["running", "pending"].includes(item.status))
        .forEach((item) => { item.status = type === "error" ? "failed" : "interrupted"; item.timestamp_end = timestamp; });
      activeAssistantLoop = null;
    }

    const kind = liveEventKind(type, payload);
    const status = type === "error" ? "failed"
      : type === "cancelled" ? "interrupted"
        : type === "final" ? normalizedStatus(payload.status)
          : type === "tool_call" ? "pending" : "running";
    liveEvents.push({
      id: `live-${type}-${payload.tool_call_id || Date.now()}-${liveEvents.length}`,
      kind,
      input_sequence: inputSequence,
      run_id: payload.run_id || currentRunId,
      timestamp,
      status,
      label: liveLabel(type, payload),
      summary: liveSummary(type, payload),
      tool_call_id: payload.tool_call_id || null,
      source: payload.source || "sse",
      payload: redactTrajectoryValue(jsonClone(type === "tool_result" ? null : payload)),
      result: redactTrajectoryValue(jsonClone(type === "tool_result" ? payload : null)),
      native: null,
    });
    scheduleRender();
  }

  function exportTrace() {
    const payload = {
      format: "deepagent-session-trajectory",
      version: 1,
      source: "history+sse",
      generated_at: new Date().toISOString(),
      session_id: state.sessionId || history.session_id || null,
      conversation_id: state.conversationId || history.conversation_id || null,
      limitations: [
        "不包含系统提示词、动态工具Schema、认证Header、Token或Cookie",
        "历史轨迹没有持久化逐Part精确耗时",
      ],
      events: allEvents(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const suffix = String(state.sessionId || "new-session").slice(0, 12);
    link.href = url;
    link.download = `deepagent-trajectory-${suffix}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  document.getElementById("chatViewTabs")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-view]");
    if (button) selectView(button.dataset.chatView);
  });
  document.querySelector(".trajectory-modes")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-trajectory-mode]");
    if (!button) return;
    mode = button.dataset.trajectoryMode;
    document.querySelectorAll("[data-trajectory-mode]").forEach((item) => item.classList.toggle("active", item === button));
    scheduleRender();
  });
  search?.addEventListener("input", scheduleRender);
  exportButton?.addEventListener("click", exportTrace);
      list.addEventListener("click", (event) => {
        const row = event.target.closest("[data-trajectory-id]");
    if (!row) return;
    selectedId = row.dataset.trajectoryId;
    detailTab = "summary";
    scheduleRender();
  });
  detail.addEventListener("click", (event) => {
    const copyCode = event.target.closest("[data-copy-run-code]");
    if (copyCode) {
      const selected = allEvents().find((item) => item.id === selectedId);
      copyTextToClipboard(runCodeSource(selected)).then(() => {
        copyCode.classList.add("copied");
        copyCode.textContent = "已复制";
      });
      return;
    }
    const eventTarget = event.target.closest("[data-trajectory-event-target]");
    if (eventTarget) {
      selectedId = eventTarget.dataset.trajectoryEventTarget;
      detailTab = "summary";
      scheduleRender();
      return;
    }
    const summaryTarget = event.target.closest("[data-trajectory-detail-tab-target]");
    if (summaryTarget) {
      detailTab = summaryTarget.dataset.trajectoryDetailTabTarget;
      scheduleRender();
      return;
    }
    const button = event.target.closest("[data-trajectory-detail-tab]");
    if (!button) return;
    detailTab = button.dataset.trajectoryDetailTab;
    scheduleRender();
  });

  render();
  return { setHistory, reset, addUser, ingest, selectView, events: allEvents };
}
