import { mountStructuredOutput } from "./structured-output.js?v=20261022-output-inspector3";
import { cacheMessageSummary } from "./cache-usage.js?v=20260923-usage-tab-2";
import { tokenValue } from "./context-budget-presentation.js?v=20260923-c02";
import { renderPlanSnapshot } from "./plan-view.js?v=20261103-view-image1";
import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import { centerInScrollContainer } from "./scroll.js?v=20260915-scroll-focus";
import { parseServerTimestamp } from "./time.js?v=20260912-21";
import { renderWebImagePreview } from "./web-image.js?v=20260912-21";
import { findViewImageResult, renderViewImageResult } from "./view-image.js?v=20261103-view-image3";
import { attachmentPreviewAttributes, attachmentDownloadUrl } from "./attachment-preview.js?v=20261103-view-image1";
import { toolResultPresentation } from "./assistant-presentation.js?v=20261103-view-image1";
import { renderPtcExecution } from "./ptc-execution.js?v=20260924-5";

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


function eventLabel(kind, part = {}) {
  if (kind === "input") return "User message";
  if (kind === "thinking") return "Thinking";
  if (kind === "assistant") return "Assistant message";
  if (kind === "title") return "Session title";
  if (kind === "error") return "异常";
  if (kind === "output") return part.name || "输出";
  if (kind === "compaction") return "Compaction";
  if (kind === "planning") return part.tool || "任务计划";
  if (kind === "deferred") return part.tool || "等待用户操作";
  if (kind === "tool") return part.tool || "工具调用";
  return part.event_type || "会话事件";
}

function eventSummary(kind, source = {}) {
  if (kind === "input") return valuePreview(source.content) || "仅包含附件或上下文引用";
  if (["thinking", "assistant"].includes(kind)) return valuePreview(source.content);
  if (kind === "title") return valuePreview(source.title) || "Session title";
  if (kind === "output") return source.accepted ? "已接受结构化输出" : valuePreview(source.errors) || "输出未被接受";
  if (kind === "error") return valuePreview(source.message) || "执行异常";
  if (kind === "compaction") {
    const before = source.budget?.estimated_tokens_before ?? source.budget?.estimated_before;
    const after = source.budget?.estimated_tokens_after ?? source.budget?.estimated_after;
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

function assistantLoopSummary(payload = {}, status = "running") {
  if (payload.content) return valuePreview(payload.content);
  if (payload.tool_calls?.length) return `调用 ${payload.tool_calls.length} 个工具`;
  if (!payload.thinking) return "模型响应";
  if (status === "failed") return "模型响应失败";
  if (status === "interrupted") return "已停止";
  if (status === "completed") return "模型思考";
  return "模型处理中";
}

function assistantMessageKey(event) {
  const ordinal = event.payload?.source?.message_ordinal;
  return event.run_id && Number.isInteger(ordinal)
    ? `${event.run_id}:${ordinal}` : event.event_id;
}

function sameTool(row, callId, name) {
  return TOOL_KINDS.has(row.kind) && row.tool_call_id === callId && (!name || row.label === name);
}

function callIdentity(row) {
  return row.native?.call_event_id ? `${row.native.call_event_id}:${row.native.call_part_offset || 0}` : row.id;
}

function pendingToolCall(rows, runId, turnId, isDeferred = () => false) {
  const sameRun = rows.filter(row => row.run_id === runId && (!turnId || !row.turn_id || row.turn_id === turnId));
  if (sameRun.length) return sameRun.length === 1 ? sameRun[0] : null;
  // Deferred answers start a new Run. An ID alone is not enough to identify
  // their original call; use a durable pending assistant source, not a widget
  // (widgets are SSE display hints and need not exist in persisted Parts).
  const deferred = rows.filter(row => isDeferred(row) && row.status === "pending"
    && turnId && row.turn_id === turnId);
  return deferred.length === 1 ? deferred[0] : null;
}

function sameToolValue(left, right) {
  const normalized = value => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
  return normalized(left) === normalized(right);
}

function sameToolArgs(left, right) {
  const parsed = value => {
    if (typeof value === "string") { try { return JSON.parse(value); } catch { /* Keep non-JSON arguments. */ } }
    return value;
  };
  return sameToolValue(parsed(left), parsed(right));
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

function applyExecutionTiming(row, timing, kind) {
  if (timing?.version !== 1 || timing.kind !== kind
    || typeof timing.duration_ms !== "number" || !Number.isFinite(timing.duration_ms) || timing.duration_ms < 0) return false;
  const start = parseServerTimestamp(timing.started_at)?.getTime();
  const end = parseServerTimestamp(timing.completed_at)?.getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return false;
  row.timestamp = timing.started_at;
  row.timestamp_end = timing.completed_at;
  row.duration_ms = timing.duration_ms;
  row.duration_source = "execution";
  row.duration_description = kind === "model_request"
    ? "模型请求耗时：包含等待响应和完整流式生成，不含工具执行；采用服务端单调时钟。"
    : "工具执行耗时：服务端实际执行区间，不含模型生成参数、人工等待及事件入库。并行工具耗时不能直接相加。";
  return true;
}

function applyLegacyInterval(row, start, end, description) {
  const a = start ? parseServerTimestamp(start)?.getTime() : NaN;
  const b = end ? parseServerTimestamp(end)?.getTime() : NaN;
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return;
  row.timestamp = start;
  row.timestamp_end = end;
  row.duration_ms = b - a;
  row.duration_source = "estimated";
  row.duration_description = description;
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

function toolResultArtifacts(metadata) {
  if (!metadata || typeof metadata !== "object") return [];
  const artifacts = [];
  if (metadata.overflow_handle) {
    artifacts.push({
      artifact_id: String(metadata.overflow_handle),
      kind: "tool_result_overflow",
      role: "return_value",
      byte_size: metadata.overflow_bytes ?? null,
      content_hash: metadata.overflow_hash || null,
    });
  }
  if (metadata.overflow_content_handle) {
    artifacts.push({
      artifact_id: String(metadata.overflow_content_handle),
      kind: "tool_result_overflow",
      role: "content",
      content_hash: metadata.overflow_content_hash || null,
    });
  }
  const seen = new Set();
  (Array.isArray(metadata.generated_files) ? metadata.generated_files : []).forEach((file) => {
    const fileId = typeof file?.file_id === "string" ? file.file_id : "";
    if (!fileId || seen.has(fileId)) return;
    seen.add(fileId);
    artifacts.push({
      artifact_id: fileId,
      file_id: fileId,
      kind: "sandbox_output",
      role: "generated_file",
      file_name: typeof file.file_name === "string" ? file.file_name : null,
      mime: typeof file.mime === "string" ? file.mime : null,
      byte_size: Number.isFinite(Number(file.byte_size)) ? Number(file.byte_size) : null,
      sha256: typeof file.sha256 === "string" ? file.sha256 : null,
    });
  });
  const evidence = metadata.document_evidence_file;
  if (typeof evidence?.file_id === "string" && evidence.file_id && !seen.has(evidence.file_id)) {
    artifacts.push({
      artifact_id: evidence.file_id,
      file_id: evidence.file_id,
      kind: "document_evidence",
      role: "generated_file",
      file_name: typeof evidence.file_name === "string" ? evidence.file_name : null,
      mime: typeof evidence.mime === "string" ? evidence.mime : null,
      byte_size: evidence.byte_size ?? null,
      sha256: typeof evidence.sha256 === "string" ? evidence.sha256 : null,
    });
  }
  return artifacts;
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

function projectExecutionError(event, inputSequence) {
  const payload = event.payload || {};
  const projected = newEvent({
    id: event.event_id, kind: "error", inputSequence: Math.max(inputSequence, 1),
    runId: event.run_id || null, timestamp: event.created_at || null,
    status: "failed", part: payload, payload,
  });
  projected.sequence = Number(event.sequence || 0);
  projected.turn_id = event.turn_id || null;
  projected.event_type = event.event_type;
  projected.timestamp_end = event.updated_at || event.created_at || null;
  return projected;
}

function outputSourceMatches(fact, event, part, offset) {
  const source = fact.payload?.source || {};
  const sourceIds = source.event_ids || [];
  const sourceIndex = Number(event.payload?.source?.part_index || 0) + offset;
  if (sourceIds.length && !sourceIds.includes(event.event_id)) return false;
  if (!sourceIds.length && (fact.run_id !== event.run_id || source.message_ordinal !== event.payload?.source?.message_ordinal)) return false;
  return part?.tool_call_id === fact.payload?.tool_call_id
    && (source.part_index == null || source.part_index === sourceIndex);
}

function projectOutputAttempt(event, inputSequence, items) {
  const payload = event.payload || {};
  let candidate, candidateFound = Object.hasOwn(payload, "output");
  if (candidateFound) candidate = payload.output;
  else {
    for (const source of items) {
      const parts = storedMessage(source)?.parts || [];
      const index = parts.findIndex((part, offset) => toolRole(part) === "call" && outputSourceMatches(event, source, part, offset));
      if (index < 0) continue;
      candidate = parts[index].args;
      candidateFound = true;
      break;
    }
  }
  return {
    ...newEvent({ id: event.event_id, kind: "output", inputSequence: Math.max(inputSequence, 1), runId: event.run_id,
      timestamp: event.created_at, status: payload.accepted ? "completed" : "failed", part: payload, payload,
      native: { event_ids: [event.event_id], source_event_ids: payload.source?.event_ids || [] },
    }),
    sequence: Number(event.sequence || 0), turn_id: event.turn_id, event_type: "output/attempt",
    tool_call_id: payload.tool_call_id, candidate, candidate_found: candidateFound,
  };
}

/** Build semantic trajectory rows from complete Message Events. */
export function buildTrajectory(history = {}) {
  const items = [...(Array.isArray(history?.items) ? history.items : [])]
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
  const deferredSources = new Set(items.filter(event => event.event_type === "assistant/message"
    && event.status === "pending").map(event => event.event_id));
  let inputSequence = 0;
  const projected = [];
  const tools = new Map();
  const outputCallsByRun = new Map();
  const assistantMessages = new Map();
  const automaticTitles = new Map();
  const outputFacts = items.filter(event => event.event_type === "output/attempt");
  for (const event of items) {
    if (event.event_type === "output/attempt") {
      projected.push(projectOutputAttempt(event, inputSequence, items));
      continue;
    }
    if (event.event_type === "execution/error") {
      projected.push(projectExecutionError(event, inputSequence));
      continue;
    }
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
      const source = event.payload?.source;
      const messageKey = assistantMessageKey(event);
      let group = assistantMessages.get(messageKey);
      if (!group) {
        group = { event, parts: new Map(), eventIds: [], row: null };
        assistantMessages.set(messageKey, group);
      }
      group.eventIds.push(event.event_id);
      parts.forEach((part, offset) => {
        group.parts.set(Number.isInteger(source?.part_index) ? source.part_index + offset : offset, part);
      });
      const assistantParts = [...group.parts.entries()].sort(([left], [right]) => left - right).map(([, part]) => {
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
      // A tool-only response is still a persisted assistant decision. Keep one
      // row per SDK message, including empty thinking, rather than hiding facts.
      const assistant = newEvent({
        id: group.event.event_id,
        kind: "assistant",
        inputSequence: Math.max(inputSequence, 1),
        runId: event.run_id || message.run_id || null,
        timestamp: group.event.created_at || message.timestamp || null,
        status: event.status || message.state || "completed",
        part: { content: assistantPayload.content },
        payload: assistantPayload,
        native: { ...messageMeta(message), event_ids: [...group.eventIds] },
      });
      assistant.summary = assistantLoopSummary(assistantPayload, assistant.status);
      assistant.sequence = Number(group.event.sequence || 0);
      assistant.turn_id = event.turn_id || null;
      assistant.event_type = event.event_type;
      // Writes happen after generation; updated_at can change again at Run
      // finalization. Neither timestamp measures a model request.
      assistant.timestamp_end = null;
      if (!applyExecutionTiming(assistant, message.metadata?.deepagent_timing, "model_request")) {
        applyLegacyInterval(assistant, message.timestamp, group.event.created_at,
          "旧记录估算：供应商响应时间戳至首次入库，可能缺少首字节前等待并包含入库等待，不是精确模型请求耗时。未使用更新时间。");
      }
      if (group.row) Object.assign(group.row, assistant);
      else {
        group.row = assistant;
        projected.push(assistant);
      }
    }

    parts.forEach((part, offset) => {
      const role = toolRole(part);
      const callId = part?.tool_call_id;
      if (!role || !callId) return;
      if (part.part_kind === "retry-prompt" && !part.tool_name && message.metadata?.deepagent_output_budget) return;
      const outputMetadata = message.metadata?.deepagent_output;
      const absolutePartIndex = Number(event.payload?.source?.part_index || 0) + offset;
      const outputCallKey = `${event.run_id || ""}:${callId}`;
      if (role === "call") outputCallsByRun.set(outputCallKey,
        (outputMetadata?.part_indices || []).includes(absolutePartIndex)
        || outputFacts.some(fact => outputSourceMatches(fact, event, part, offset)));
      // SDK RetryPrompt may omit tool_name. Pair it with the nearest actual call
      // in this Run, rather than creating a normal tool or guessing by its name.
      if (outputCallsByRun.get(outputCallKey)) return;
      const toolName = part.tool_name || null;
      const sameId = tools.get(callId) || [];
      const matching = sameId.filter(row => sameTool(row, callId, toolName));
      let tool = role === "result" ? pendingToolCall(matching.filter(row => row.native?.call_event_id
        && !row.native?.result_event_id), event.run_id, event.turn_id,
        row => Boolean(toolName) && deferredSources.has(row.native.call_event_id)) : null;
      const repeatedCall = sameId.length > 0;
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
          id: repeatedCall ? `tool:${callId}:${event.event_id}:${offset}` : `tool:${callId}`,
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
        const dispatch = message.metadata?.deepagent_dispatch;
        const partIndex = Number(event.payload?.source?.part_index || 0) + offset;
        tool.dispatched = !(dispatch?.rejected === true && (dispatch.not_dispatched_part_indices || []).includes(partIndex));
        if (!tool.dispatched) { tool.status = "failed"; tool.summary = "未分发：模型响应被拒绝"; }
        tool.timestamp_end = event.updated_at || tool.timestamp;
        sameId.push(tool);
        tools.set(callId, sameId);
        projected.push(tool);
      }
      if (role === "call") {
        tool.kind = partKind({ type: "tool_call", widget: part.metadata?.widget, metadata: part.metadata }, toolName);
        tool.label = eventLabel(tool.kind, { tool: toolName });
        tool.payload = redactTrajectoryValue(jsonClone({ args: part.args ?? null, metadata: part.metadata || null }));
        tool.native = { ...(tool.native || {}), call_event_id: event.event_id, call_part_offset: offset };
      } else {
        const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
        tool.status = tool.dispatched === false ? "failed" : toolResultPresentation(part).status;
        tool.result = redactTrajectoryValue(jsonClone({
          result: part.content ?? null,
          outcome: part.outcome || null,
          plan: toolName === "write_plan" ? metadata.deepagent_plan || null : null,
          artifacts: toolResultArtifacts(metadata),
          metadata: { code_mode: metadata.code_mode, nested_calls: metadata.nested_calls, ptc_diagnostics: metadata.ptc_diagnostics, ...(toolName === "view_image" ? { view_image: metadata.view_image } : {}) },
        }));
        tool.native = { ...(tool.native || {}), result_event_id: event.event_id, result_part_offset: offset };
        tool.timestamp_end = null;
        const isDeferredResume = tool.run_id !== event.run_id && deferredSources.has(tool.native.call_event_id);
        if (!applyExecutionTiming(tool, metadata.deepagent_timing, "tool_execution")
          && tool.kind !== "deferred" && !isDeferredResume) {
          applyLegacyInterval(tool, tool.timestamp, part.timestamp,
            "旧记录估算：工具调用入库至结果产生，包含调度等待，不是精确执行耗时。未使用更新时间。");
        }
        tool.summary = tool.dispatched === false ? "未分发：模型响应被拒绝" : eventSummary(tool.kind, { type: "tool_result", result: part.content });
      }
    });
  }
  // Pending records describe the pause at persistence time. Resolve only calls
  // belonging to this response, not an unrelated later run with the same ID.
  const toolsByCallEvent = new Map();
  for (const tool of projected.filter((row) => TOOL_KINDS.has(row.kind))) {
    const id = tool.native?.call_event_id;
    if (!id) continue;
    const group = toolsByCallEvent.get(id) || [];
    group.push(tool);
    toolsByCallEvent.set(id, group);
  }
  for (const row of projected) {
    if (row.kind !== "assistant" || row.status !== "pending" || !row.payload?.tool_calls?.length) continue;
    const matching = (row.native?.event_ids || [row.id]).flatMap((id) => toolsByCallEvent.get(id) || []);
    if (row.payload.tool_calls.every((call) => matching.some((tool) => tool.tool_call_id === call.tool_call_id && tool.native?.result_event_id))) {
      row.status = "completed";
    }
  }
  return projected.sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
}

export function trajectoryStats(events = []) {
  return {
    inputs: new Set(events.map((event) => event.input_sequence).filter(Boolean)).size,
    calls: events.filter((event) => (
      TOOL_KINDS.has(event.kind)
      && event.event_type === "tool/interaction"
      && event.native?.call_event_id
      && event.dispatched !== false
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
  if (["context_compaction", "context_compaction_progress", "context_compaction_result"].includes(type)) return "compaction";
  if (type === "tool_call" || type === "tool_result" || type === "tool_call_removed") {
    if (data?.source === "planning") return "planning";
    if (data?.widget) return "deferred";
    return "tool";
  }
  return type;
}

function liveLabel(type, data) {
  if (type.startsWith("context_compaction")) return "Compaction";
  return eventLabel(liveEventKind(type, data), data);
}

function liveSummary(type, data) {
  if (["thinking", "text"].includes(type)) return valuePreview(data?.delta);
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
  return {
    input: "用户",
    assistant: "助手",
    thinking: "思考",
    title: "标题",
    tool: "工具",
    planning: "计划",
    deferred: "交互",
    compaction: "压缩",
    error: "异常",
    output: "输出",
  }[kind] || String(kind || "事件");
}

function isRunCodeEvent(event) {
  return event?.kind === "tool" && event?.label === "run_code";
}

function isPythonCodeEvent(event) {
  return event?.kind === "tool" && ["run_code", "execute_python"].includes(event?.label);
}

function eventKindClass(event) {
  return isRunCodeEvent(event) ? "code" : event?.kind;
}

function eventKindBadge(event) {
  return isRunCodeEvent(event) ? "PTC" : kindBadge(event?.kind);
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
  if (event.duration_ms != null && Number.isFinite(Number(event.duration_ms))) return Number(event.duration_ms);
  if (["input", "error", "title"].includes(event.kind)) return null;
  if (["assistant", "tool", "code", "ui", "ui_tool"].includes(event.kind) || event.tool_call_id) return null;
  const started = event.timestamp ? parseServerTimestamp(event.timestamp)?.getTime() : NaN;
  const ended = event.timestamp_end ? parseServerTimestamp(event.timestamp_end)?.getTime() : NaN;
  return Number.isFinite(started) && Number.isFinite(ended) ? Math.max(0, ended - started) : null;
}

function durationLabel(value) {
  if (!Number.isFinite(value)) return null;
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

function renderRowTime(event, clockMode) {
  if (clockMode !== "duration") return `<time>${escapeHtml(formatTime(event.timestamp))}</time>`;
  const duration = eventDuration(event);
  if (duration == null) return '<span class="trajectory-duration unknown" title="没有可确认的执行耗时；瞬时事件和人工等待不计为0秒">—</span>';
  const band = duration <= 5000 ? "fast" : duration <= 10000 ? "steady" : duration <= 20000 ? "elevated" : "slow";
  const range = {fast:"≤5秒",steady:"5–10秒",elevated:"10–20秒",slow:">20秒"}[band];
  const label = durationLabel(duration);
  return `<span class="trajectory-duration ${band}" title="${escapeHtml(`${range}；${event.duration_description || "服务端记录的耗时"}`)}"><i aria-hidden="true"></i>${escapeHtml(label)}</span>`;
}

function renderMarkdown(content, emptyMessage = "No content in this event.") {
  if (!content) return `<p class="trajectory-summary-empty">${escapeHtml(emptyMessage)}</p>`;
  const rendered = globalThis.DeepAgentMarkdown?.renderBody?.(content)
    || `<div class="markdown-body"><p>${escapeHtml(content)}</p></div>`;
  return `<div class="trajectory-summary-markdown">${rendered}</div>`;
}

function trajectoryUsage(event) {
  if (!event.native?.usage) return null;
  const usage = event.native.usage;
  const input = tokenValue(usage.input_tokens);
  const output = tokenValue(usage.output_tokens);
  const reportedTotal = tokenValue(usage.total_tokens);
  const cache = cacheMessageSummary(event.native);
  const reported = [input, output, reportedTotal, cache.cache_read_tokens, cache.cache_write_tokens]
    .some(value => value != null && value > 0) || cache.read_complete || cache.write_complete;
  if (!reported) return null;
  return { input, output, total: reportedTotal ?? (input != null && output != null ? input + output : null), cache };
}

export function renderTrajectoryUsage(event) {
  const usage = trajectoryUsage(event);
  if (!usage) return `<p class="trajectory-summary-empty">此请求暂无供应商用量数据。</p>`;
  const { input, output, total, cache } = usage;
  const number = value => value == null ? "—" : value.toLocaleString("zh-CN");
  const fact = (label, text, title = "") => `<span title="${escapeHtml(title || `${label} ${text}`)}"><small>${label}</small><strong>${escapeHtml(text)}</strong></span>`;
  return `<section class="inspector-section">
    <header><strong>Token 用量</strong></header>
    <div class="context-budget-summary">${fact("输入", number(input))}${fact("输出", number(output))}${fact("合计", number(total), `合计 ${number(total)} Token`)}</div>
    <p class="trajectory-summary-copy">本次模型请求的用量，同一响应拆分的事件共用此统计。</p>
  </section>
  <section class="inspector-section">
    <header><strong>缓存统计</strong></header>
    <div class="context-budget-summary">${fact("命中率", cache.hit_ratio == null ? "—" : `${(cache.hit_ratio * 100).toFixed(1)}%`, "缓存读取 ÷ 输入 Token；未报告或无法计算时显示 —")}${fact("缓存读取", number(cache.cache_read_tokens), cache.cache_read_tokens == null ? "供应商未报告或旧记录无法确认" : `缓存读取 ${number(cache.cache_read_tokens)} Token`)}${fact("缓存写入", number(cache.cache_write_tokens), cache.cache_write_tokens == null ? "供应商未报告或旧记录无法确认" : `缓存写入 ${number(cache.cache_write_tokens)} Token`)}</div>
    <p class="trajectory-summary-copy">缓存 Token 已包含在输入中。</p>
  </section>`;
}

function renderMeta(event) {
  const duration = eventDuration(event);
  const usage = trajectoryUsage(event);
  const items = [
    ["clock-3", event.timestamp ? formatTime(event.timestamp) : null],
    ["timer", duration == null && (event.kind === "assistant" || event.tool_call_id) ? "Duration unknown" : durationLabel(duration)],
    ["coins", usage?.total != null ? `${usage.total.toLocaleString("zh-CN")} Token` : null],
  ].filter(([, value]) => value);
  if (!items.length) return "";
  return `<div class="trajectory-summary-meta">${items.map(([name, value]) => `<span title="${escapeHtml(name === "coins" ? "Total tokens for this model request" : name === "timer" ? event.duration_description || "Execution timestamps unavailable; event creation or update time is not execution duration." : "Event time")}">${icon(name)}${escapeHtml(value)}</span>`).join("")}</div>`;
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

function renderInputSummary(event, _events, attachmentUrl = attachmentDownloadUrl) {
  const contextRefs = Array.isArray(event.payload?.context_refs) ? event.payload.context_refs : [];
  const attachments = Array.isArray(event.payload?.attachments) ? event.payload.attachments : [];
  const contextLinks = contextRefs.map((reference, index) => {
    const safe = redactTrajectoryValue(reference);
    const label = safe?.label || safe?.key || safe?.type || "Unnamed context";
    const identity = `${index}:${safe?.key || ""}`;
    const type = [safe?.type, safe?.schema_version ? `v${safe.schema_version}` : null].filter(Boolean).join(" · ");
    return `<details class="trajectory-context-reference" data-trajectory-context="${escapeHtml(identity)}">
      <summary class="trajectory-context-heading">${icon("braces")}<span class="trajectory-context-copy"><strong><span class="trajectory-context-index">${index + 1}.</span> ${escapeHtml(label)}</strong><small>${escapeHtml(type)}</small>${safe?.key ? `<code>${escapeHtml(safe.key)}</code>` : ""}</span>${icon("chevron-right")}</summary>
      <div class="trajectory-context-content">${renderJsonBlock(safe, { expanded: true })}</div>
    </details>`;
  }).join("");
  const attachmentLinks = attachments.map((attachment) => {
    const href = attachmentUrl(attachment);
    const name = attachment.name || attachment.id || "Unnamed attachment";
    const detail = attachment.mime_type || attachment.mime || "Attachment";
    const previewAttributes = attachmentPreviewAttributes(attachment, undefined, href);
    const copy = `<span><strong>${escapeHtml(name)}</strong><small>${escapeHtml(detail)}</small></span>`;
    const download = href ? `<a class="trajectory-attachment-download" href="${escapeHtml(href)}" download aria-label="下载 ${escapeHtml(name)}" title="下载">${icon("download")}</a>` : "";
    const main = previewAttributes
      ? `<button type="button" class="trajectory-summary-link attachment-image-trigger" ${previewAttributes}>${icon("image")}${copy}</button>`
      : href ? `<a class="trajectory-summary-link" href="${escapeHtml(href)}" download>${icon("paperclip")}${copy}</a>`
      : renderJumpLink({ eventId: null, iconName: "paperclip", text: name, detail });
    return `<div class="trajectory-attachment-row">${main}${download}</div>`;
  }).join("");
  return `${renderSummarySection(
    "Preview",
    renderMarkdown(event.payload?.content, "This message only contains attachments or context references."),
    { targetTab: "preview", className: "trajectory-summary-preview is-scroll" },
  )}${attachmentLinks ? renderSummarySection("Attachments", `<div class="trajectory-summary-links">${attachmentLinks}</div>`) : ""}
    ${contextLinks ? renderSummarySection("Context references", `<div class="trajectory-summary-links">${contextLinks}</div>`) : ""}`;
}

function renderThinking(thinking) {
  if (!thinking) return "";
  return `<details class="trajectory-thinking assistant-thinking">
    <summary class="assistant-thinking-head"><span class="thinking-icon">${icon("brain")}</span><span class="thinking-heading">思考过程</span><span class="thinking-chevron">${icon("chevron-right")}</span></summary>
    <div class="trajectory-thinking-content">${escapeHtml(thinking)}</div>
  </details>`;
}

function renderAssistantTools(event, events) {
  const calls = Array.isArray(event.payload?.tool_calls) ? event.payload.tool_calls : [];
  if (!calls.length) return "";
  return `<div class="trajectory-summary-links">${calls.map((call) => {
    const target = [...events].reverse().find((item) => (TOOL_KINDS.has(item.kind) || item.kind === "output")
      && item.tool_call_id === call?.tool_call_id
      && (event.source === "sse"
        ? item.run_id === event.run_id
        : item.kind === "output"
          ? (item.native?.source_event_ids || []).some(id => (event.native?.event_ids || [event.id]).includes(id))
          : (event.native?.event_ids || [event.id]).includes(item.native?.call_event_id)));
    return renderJumpLink({
      eventId: target?.id || null,
      iconName: target?.kind === "output" ? "braces" : "wrench",
      text: call?.tool || "Unnamed tool",
      detail: valuePreview(call?.args, 120),
    });
  }).join("")}</div>`;
}

function renderAssistantSummary(event, events) {
  const preview = `${renderThinking(event.payload?.thinking)}${renderMarkdown(
    event.payload?.content,
    event.payload?.thinking ? "No text output yet." : "No text in this model response.",
  )}`;
  return `${renderSummarySection(
    "Preview",
    preview,
    { targetTab: "preview", className: "trajectory-summary-preview trajectory-summary-assistant-preview is-scroll" },
  )}
    ${event.payload?.tool_calls?.length ? renderSummarySection("Tool", renderAssistantTools(event, events)) : ""}`;
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

function renderLocalFileDownload(value, artifacts = [], imageOptions = {}) {
  if (findViewImageResult(value, imageOptions)) return "";
  if (artifacts.some((item) => item?.kind === "sandbox_output")) return "";
  const file = localFileDownload(value);
  if (!file || artifacts.some((item) => (item?.file_id || item?.artifact_id) === file.fileId)) return "";
  const path = `api/v1/local-files/${encodeURIComponent(file.fileId)}/download`;
  const href = imageOptions.attachmentUrl?.({ id: file.fileId, name: file.fileName }) || globalThis.serviceUrl?.(path) || `/${path}`;
  return renderSummarySection(
    "Files",
    `<a class="trajectory-summary-link local-file-download" href="${escapeHtml(href)}" download>${icon("download")}<span><strong>${escapeHtml(file.fileName)}</strong></span></a>`,
  );
}

function formatFileBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${Math.round(bytes)} B`;
}

function renderGeneratedFiles(artifacts, attachmentUrl) {
  const seen = new Set();
  const rows = artifacts.map((artifact) => {
    if (!["sandbox_output", "document_evidence", "tool_result_overflow"].includes(artifact?.kind)) return "";
    const fileId = artifact.file_id || artifact.artifact_id;
    if (typeof fileId !== "string" || !fileId || seen.has(fileId)) return "";
    seen.add(fileId);
    const fileName = artifact.file_name || (artifact.kind === "tool_result_overflow"
      ? (artifact.role === "content" ? "完整工具内容" : "完整工具结果") : fileId);
    const details = [artifact.mime, formatFileBytes(artifact.byte_size)].filter(Boolean).join(" · ");
    const file = { id: fileId, name: artifact.file_name, mime: artifact.mime };
    const path = `api/v1/local-files/${encodeURIComponent(fileId)}/download`;
    const downloadUrl = attachmentUrl(file) || (fileId.startsWith("local_") ? globalThis.serviceUrl?.(path) || `/${path}` : null);
    const preview = downloadUrl ? attachmentPreviewAttributes(file, undefined, downloadUrl) : "";
    const copy = `<strong>${escapeHtml(fileName)}</strong>${details ? `<small>${escapeHtml(details)}</small>` : ""}`;
    const label = preview
      ? `<button type="button" class="trajectory-summary-link attachment-image-trigger" ${preview}>${icon("image")}<span>${copy}</span></button>`
      : copy;
    const download = downloadUrl
      ? `<a class="local-file-download" href="${escapeHtml(downloadUrl)}" download aria-label="下载 ${escapeHtml(fileName)}" title="下载文件">${icon("download")}</a>`
      : "";
    return `<div class="generated-file-item"><span class="generated-file-icon">${icon("file-output")}</span><span class="generated-file-copy">${label}<code title="${escapeHtml(fileId)}">${escapeHtml(fileId)}</code></span><span class="generated-file-actions">${download}<button type="button" data-copy-file-id="${escapeHtml(fileId)}" title="复制 File ID">${icon("copy")}</button></span></div>`;
  }).join("");
  return rows ? renderSummarySection("工具产物", `<div class="generated-file-list">${rows}</div>`) : "";
}

function renderPayloadResultSections({ payload, result }) {
  const payloadText = hasMeaningfulValue(payload) ? valuePreview(payload, 280) : null;
  const resultText = hasMeaningfulValue(result) ? valuePreview(result, 360) : null;
  return `${payloadText ? renderSummarySection("Input", `<p class="trajectory-summary-code">${escapeHtml(payloadText)}</p>`, { targetTab: "input" }) : ""}
    ${resultText ? renderSummarySection("Result", `<p class="trajectory-summary-code">${escapeHtml(resultText)}</p>`, { targetTab: "result" }) : ""}`;
}

function renderToolSummary(event, _events, attachmentUrl = attachmentDownloadUrl) {
  const result = toolResultValue(event);
  const artifacts = Array.isArray(event.result?.artifacts) ? event.result.artifacts : [];
  // Tool event labels are projected from the actual tool name, never its result.
  const imageOptions = { toolName: event.label, metadata: event.result?.metadata, attachmentUrl };
  const imageResults = renderViewImageResult(result, imageOptions);
  const imagePreview = imageResults ? "" : renderWebImagePreview(result, { className: "trajectory-web-image-preview" });
  return `${imageResults ? renderSummarySection("查看图片", imageResults) : ""}${imagePreview ? renderSummarySection("Web image", imagePreview) : ""}${renderGeneratedFiles(artifacts, attachmentUrl)}${renderPayloadResultSections({
    payload: event.payload?.args ?? event.payload,
    result,
  })}${renderLocalFileDownload(result, artifacts, imageOptions)}`;
}

function planSnapshot(event) {
  return event.result?.plan || event.payload?.plan || event.result?.result?.plan || null;
}

function renderPlanningSummary(event) {
  const plan = planSnapshot(event);
  return `${renderPlanSnapshot(plan)}
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
  const before = event.payload?.budget?.estimated_tokens_before ?? event.payload?.budget?.estimated_before;
  const after = event.payload?.budget?.estimated_tokens_after ?? event.payload?.budget?.estimated_after;
  const primary = before != null && after != null
    ? `${Number(before).toLocaleString("zh-CN")} → ${Number(after).toLocaleString("zh-CN")} Token`
    : event.summary;
  const source = event.payload?.source || {};
  const detail = [
    source.through_sequence != null ? `Through sequence ${source.through_sequence}` : null,
    event.payload?.summary_call?.model_id || null,
    event.payload?.error?.message || event.payload?.error?.code || (typeof event.payload?.error === "string" ? event.payload.error : null),
  ].filter(Boolean).join(" · ");
  return renderSummarySection("Compaction", `<p class="trajectory-summary-lead"><strong>${escapeHtml(primary || event.label)}</strong></p>${detail ? `<p class="trajectory-summary-copy">${escapeHtml(detail)}</p>` : ""}`);
}

function renderTitleSummary(event) {
  const sourceLabel = {
    fallback: "fallback",
    model: "model",
    user: "user",
  }[event.payload?.source] || event.payload?.source;
  const model = event.payload?.model_name || event.payload?.model_id;
  const facts = [
    renderFact("Source", sourceLabel),
    renderFact("Model", model, { mono: true }),
    renderFact("Source event", event.payload?.source_event_id, { mono: true }),
  ].join("");
  return renderSummarySection(
    "Title",
    `<p class="trajectory-summary-lead"><strong>${escapeHtml(event.payload?.title || event.summary || "Session title")}</strong></p>${facts ? `<div class="trajectory-context-standard">${facts}</div>` : ""}`,
  );
}

function renderExecutionErrorSummary(event) {
  return renderSummarySection("Error", `<p class="trajectory-summary-copy">${escapeHtml(event.payload?.message || event.summary)}</p>
    ${renderFact("Error code", event.payload?.code, { mono: true })}
    ${renderFact("Phase", event.payload?.phase)}`);
}

const SUMMARY_RENDERERS = Object.freeze({
  input: renderInputSummary,
  assistant: renderAssistantSummary,
  planning: renderPlanningSummary,
  deferred: renderDeferredSummary,
  tool: renderToolSummary,
  compaction: renderCompactionSummary,
  title: renderTitleSummary,
  error: renderExecutionErrorSummary,
  output: renderOutputSummary,
});

function renderOutputSummary(event) {
  const preview = event.candidate_found
    ? `<p class="trajectory-summary-code">${escapeHtml(valuePreview(formatJson(event.candidate), 360))}</p>`
    : '<p class="trajectory-summary-empty">Original candidate unavailable.</p>';
  return renderSummarySection("Output", preview, { targetTab: "output", className: "trajectory-summary-preview is-scroll" })
    + (event.payload?.errors?.length ? renderSummarySection("Validation", renderJsonBlock(event.payload.errors)) : "");
}

function renderSummary(event, events, attachmentUrl) {
  const renderer = SUMMARY_RENDERERS[event.kind];
  const primary = renderer
    ? renderer(event, events, attachmentUrl)
    : renderSummarySection("Event", `<p class="trajectory-summary-lead">${escapeHtml(event.summary || event.label)}</p>`);
  return `<section class="trajectory-summary">${renderMeta(event)}<div class="trajectory-summary-primary">${primary}</div></section>`;
}

function renderJsonBlock(value, { expanded } = {}) {
  return globalThis.DeepAgentJson?.renderBlock?.(value, {
    className: "trajectory-json is-flat",
    ariaLabel: "Trajectory JSON",
    expanded,
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

function renderRunCode(event, line = null, callName = null, compact = false) {
  const code = runCodeSource(event);
  if (!code) return `<p class="trajectory-summary-empty">No source code in this call.</p>`;
  const lines = code.split("\n");
  const exactLine = Number.isInteger(line) && line > 0 && line <= lines.length ? line : null;
  const escapedName = callName ? String(callName).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") : "";
  const callPattern = escapedName ? new RegExp(`(?:^|[^\\w])${escapedName}\\s*\\(`) : null;
  const matchingLines = callPattern && !exactLine
    ? lines.map((text, index) => callPattern.test(text) ? index + 1 : null).filter(Boolean) : [];
  const displayed = exactLine || callName
    ? lines.map((text, index) => `<span class="trajectory-code-line${index + 1 === exactLine ? " is-error" : matchingLines.includes(index + 1) ? " is-call-match" : ""}">${highlightPython(text) || " "}</span>`).join("\n")
    : highlightPython(code);
  return `<section class="trajectory-code-view">
    ${compact ? "" : `<header><span>Python</span><button type="button" data-copy-run-code>${icon("copy")}Copy</button></header>`}
    <pre><code class="hljs language-python">${displayed}</code></pre>
  </section>`;
}

// Tabs describe semantic projections, not HTTP bodies or database columns.
function detailTabs(event) {
  const tabs = [["summary", "Summary"]];
  if (event.kind === "output") return [...tabs, ["output", "Output"], ["raw", "Raw"]];
  if (trajectoryUsage(event)) tabs.push(["usage", "Usage"]);
  if (isPythonCodeEvent(event)) tabs.push(["code", "Code"]);
  if (isRunCodeEvent(event)) tabs.push(["execution", "Calls"]);
  if (TOOL_KINDS.has(event.kind)) {
    if (hasMeaningfulValue(event.payload)) tabs.push(["input", "Input"]);
    if (hasMeaningfulValue(event.result)) tabs.push(["result", "Result"]);
  } else if (["input", "assistant"].includes(event.kind)) {
    tabs.push(["message", "Message"], ["preview", "Preview"]);
    tabs.push(["raw", "Raw"]);
  } else if (hasMeaningfulValue(event.payload)) {
    tabs.push(["event", "Event"]);
  }
  return tabs;
}

function renderRaw(event) {
  if (event.kind === "assistant") {
    const sections = [
      ["Thinking", event.payload?.thinking],
      ["Text", event.payload?.content],
    ].filter(([, content]) => content);
    if (!sections.length) return `<p class="trajectory-summary-empty">No raw text.</p>`;
    return `<div class="trajectory-raw-sections">${sections.map(([title, content]) => `
      <section class="trajectory-raw-section">
        <div class="trajectory-raw-heading">${escapeHtml(title)}</div>
        <pre>${escapeHtml(content)}</pre>
      </section>`).join("")}</div>`;
  }
  const content = event.kind === "input" ? event.payload?.content : formatJson(event.payload);
  return `<pre class="trajectory-raw">${escapeHtml(content || "None")}</pre>`;
}

function renderPreview(event) {
  if (event.kind === "assistant") {
    return `<section class="trajectory-preview">${renderThinking(event.payload?.thinking)}${renderMarkdown(event.payload?.content, "No text in this model response.")}</section>`;
  }
  return `<section class="trajectory-preview">${renderMarkdown(event.payload?.content, "No text in this user message.")}</section>`;
}

function renderDetail(event, activeTab = "summary", events = [], attachmentUrl, ptcState = {}) {
  if (!event) return `<div class="trajectory-detail-empty">${icon("scan-search")}<p>Select an event to inspect</p></div>`;
  const tabs = detailTabs(event);
  const allowedTabs = new Set(tabs.map(([id]) => id));
  if (!allowedTabs.has(activeTab)) activeTab = "summary";
  let content = renderSummary(event, events, attachmentUrl);
  if (["input", "message", "event"].includes(activeTab)) content = renderJsonBlock(event.payload);
  if (activeTab === "event" && event.kind === "title" && event.native?.event_ids?.length > 1) {
    // These are title lifecycle records, never an HTTP request/response pair.
    content = renderJsonBlock({
      initial: { event_id: event.native.event_ids[0], payload: event.native.title_request },
      latest: { event_id: event.native.event_ids.at(-1), payload: event.native.title_result || event.payload },
    });
  }
  if (activeTab === "result") content = renderJsonBlock(event.result);
  if (activeTab === "output" && event.kind === "output") content = event.candidate_found
    ? '<div data-structured-output-detail></div>' : '<p class="trajectory-summary-empty">Original candidate unavailable.</p>';
  if (activeTab === "preview") content = renderPreview(event);
  if (activeTab === "raw") content = renderRaw(event);
  if (activeTab === "usage") content = renderTrajectoryUsage(event);
  if (activeTab === "code") content = renderRunCode(event);
  if (activeTab === "execution") content = renderPtcExecution(event, { ...ptcState, codeView: (step) => renderRunCode(event, step.source_line, step.tool_name, true) });
  const headerLabel = TOOL_KINDS.has(event.kind) || ["compaction", "title", "output"].includes(event.kind)
    ? event.label
    : event.run_id ? `Run ${String(event.run_id).slice(0, 8)}` : "";
  const detailBodyClass = activeTab === "code"
    ? "trajectory-detail-body is-code"
    : activeTab === "execution" ? "trajectory-detail-body is-ptc" : "trajectory-detail-body";
  return `
    <header class="trajectory-detail-head">
      <div><span class="trajectory-kind ${escapeHtml(eventKindClass(event))}">${escapeHtml(eventKindBadge(event))}</span>${headerLabel ? `<strong>${escapeHtml(headerLabel)}</strong>` : ""}<em class="${escapeHtml(event.status)}">${escapeHtml(event.kind === "output" && event.status === "failed" ? "Error" : statusLabel(event.status))}</em></div>
    </header>
    <nav class="trajectory-detail-tabs" role="tablist">${tabs.map(([id, label]) => `<button type="button" class="${activeTab === id ? "active" : ""}" data-trajectory-detail-tab="${id}">${label}</button>`).join("")}</nav>
    <div class="${detailBodyClass}">${content}</div>`;
}

export function createTrajectoryController({ state = {}, host = null, readOnly = false, attachmentUrl = attachmentDownloadUrl, onViewChange = null, onInspector = null, onOpenInspector = null, onSelectEvent = null } = {}) {
  const scope = host || document;
  const byId = (id) => host ? host.querySelector(`[data-session-role="${id}"]`) : document.getElementById(id);
  const query = (selector) => scope.querySelector(selector);
  const queryAll = (selector) => scope.querySelectorAll(selector);
  const bindings = [];
  const listen = (element, event, handler) => { if (element) { element.addEventListener(event, handler); bindings.push(() => element.removeEventListener(event, handler)); } };
  let disposed = false;
  let animationFrame = null;
  let activeView = "conversation";
  const root = byId("trajectoryView");
  const list = byId("trajectoryList");
  const detail = byId("inspectorContent");
  const conversationTab = byId("conversationViewTab");
  const trajectoryTab = byId("trajectoryViewTab");
  const search = byId("trajectorySearch");
  const exportButton = byId("exportTrajectoryBtn");
  const clockToggle = byId("trajectoryClockToggle");
  let exportReady = !readOnly;
  function setExportReady(ready) {
    exportReady = Boolean(ready);
    if (exportButton) {
      exportButton.disabled = !exportReady;
      exportButton.title = exportReady ? "导出轨迹" : "轨迹完整读取后可导出";
    }
  }
  setExportReady(exportReady);
  if (!root || !list || !detail || !conversationTab || !trajectoryTab) return null;

  let history = { items: [] };
  let historyEvents = [];
  let liveEvents = [];
  let currentRunId = null;
  const runTurns = new Map();
  let pendingResults = [];
  let liveCalls = new WeakMap();
  let activeAssistantLoop = null;
  let assistantLoopSequence = 0;
  let selectedId = null;
  let selectionPinned = false;
  let detailTab = "summary";
  let ptcVisible = 20;
  let ptcExpanded = null;
  let ptcDetailTab = "result";
  let mode = "sequence";
  let clockMode = "time";
  let renderQueued = false;
  let renderedSelectedId = null, renderedDetailTab = null;

  const allEvents = () => [...historyEvents, ...liveEvents].sort((left, right) => {
    const leftSequence = Number.isFinite(Number(left.sequence)) ? Number(left.sequence) : Number.MAX_SAFE_INTEGER;
    const rightSequence = Number.isFinite(Number(right.sequence)) ? Number(right.sequence) : Number.MAX_SAFE_INTEGER;
    return leftSequence - rightSequence;
  });

  function selectView(view, { notify = true } = {}) {
    if (disposed) return;
    const changed = activeView !== view;
    activeView = view === "trajectory" ? "trajectory" : "conversation";
    const trajectoryActive = view === "trajectory";
    query("main.chat")?.classList.toggle("trajectory-active", trajectoryActive);
    root.classList.toggle("hidden", !trajectoryActive);
    byId("messages")?.classList.toggle("hidden", trajectoryActive);
    query("main.chat > .composer")?.classList.toggle("hidden", trajectoryActive);
    conversationTab.classList.toggle("active", !trajectoryActive);
    trajectoryTab.classList.toggle("active", trajectoryActive);
    conversationTab.setAttribute("aria-selected", String(!trajectoryActive));
    trajectoryTab.setAttribute("aria-selected", String(trajectoryActive));
    byId("inspectorPane")?.classList.toggle("trajectory-inspector", trajectoryActive);
    if (trajectoryActive) {
      if (onOpenInspector) onOpenInspector();
      else if (!host) window.DeepAgentConsoleWorkspace?.openInspectorPane?.();
      scheduleRender();
    } else {
      if (onInspector) onInspector();
      else if (!host) window.DeepAgentConsoleWorkspace?.renderInspector?.();
    }
    if (changed && notify) onViewChange?.(activeView);
  }

  function scheduleRender() {
    if (disposed || renderQueued) return;
    renderQueued = true;
    animationFrame = window.requestAnimationFrame(() => {
      animationFrame = null;
      renderQueued = false;
      if (!disposed) render();
    });
  }

  function renderRows(events) {
    if (!events.length) {
      list.innerHTML = `<div class="trajectory-empty">${icon("route")}<p>当前筛选下没有轨迹。</p></div>`;
      return;
    }
    const html = [];
    events.forEach((event) => {
      const showToolName = TOOL_KINDS.has(event.kind) || event.kind === "output";
      const summary = event.summary || "No content";
      html.push(`<button type="button" class="trajectory-row ${escapeHtml(event.kind)} ${selectedId === event.id ? "active" : ""}" data-trajectory-id="${escapeHtml(event.id)}">
        <span class="trajectory-row-copy"><span class="trajectory-kind ${escapeHtml(eventKindClass(event))}">${escapeHtml(eventKindBadge(event))}</span>${showToolName ? `<strong>${escapeHtml(event.label)}</strong>` : ""}<span class="trajectory-row-summary">${escapeHtml(summary)}</span></span>
        <span class="trajectory-row-meta">${renderRowTime(event, clockMode)}<em class="${escapeHtml(event.status)}">${escapeHtml(event.kind === "output" && event.status === "failed" ? "Error" : statusLabel(event.status))}</em></span>
      </button>`);
    });
    list.innerHTML = html.join("");
  }

  function render() {
    const listScrollTop = list.scrollTop;
    const detailScrollTop = detail.scrollTop;
    const ptcChartScrollTop = detail.querySelector?.(".trajectory-ptc-chart")?.scrollTop || 0;
    const ptcJsonScrollTop = detail.querySelector?.(".trajectory-ptc-json-panel")?.scrollTop || 0;
    const events = allEvents();
    const stats = trajectoryStats(events);
    const visible = filterTrajectory(events, { mode, query: search?.value });
    if (clockToggle) {
      clockToggle.innerHTML = `${icon(clockMode === "duration" ? "timer" : "clock-3")}<span>${clockMode === "duration" ? "耗时" : "时间"}</span>`;
      clockToggle.setAttribute("aria-pressed", String(clockMode === "duration"));
      clockToggle.setAttribute("aria-label", clockMode === "duration" ? "切换为时间显示" : "切换为耗时显示");
      clockToggle.title = clockMode === "duration" ? "切换为时间；耗时分档：≤5秒、5–10秒、10–20秒、>20秒；橙色表示较慢，非错误" : "切换为耗时：≤5秒、5–10秒、10–20秒、>20秒";
    }
    if (byId("trajectoryCallCount")) byId("trajectoryCallCount").textContent = String(stats.calls);
    const selected = events.find((event) => event.id === selectedId) || visible[0] || null;
    selectedId = selected?.id || null;
    renderRows(visible);
    if (query("main.chat")?.classList.contains("trajectory-active")) {
      const codeState = selectedId === renderedSelectedId && detailTab === renderedDetailTab ? globalThis.DeepAgentMarkdown?.captureCodeState?.(detail) : [];
      const openContexts = selectedId === renderedSelectedId && detailTab === renderedDetailTab
        ? new Set(Array.from(detail.querySelectorAll?.("details[data-trajectory-context]") || []).filter(item => item.open).map(item => item.dataset.trajectoryContext)) : new Set();
      detail.innerHTML = renderDetail(selected, detailTab, events, attachmentUrl, { visible: ptcVisible, expanded: ptcExpanded, detailTab: ptcDetailTab });
      for (const item of detail.querySelectorAll?.("details[data-trajectory-context]") || []) item.open = openContexts.has(item.dataset.trajectoryContext);
      const outputHost = detail.querySelector?.("[data-structured-output-detail]");
      if (outputHost && selected?.candidate_found) mountStructuredOutput(outputHost, { id: selected.id, name: selected.payload.name, output: selected.candidate, failed: !selected.payload.accepted });
      globalThis.DeepAgentMarkdown?.restoreCodeState?.(detail, codeState);
      globalThis.DeepAgentMarkdown?.renderDiagrams?.(detail);
      if (selectedId === renderedSelectedId) {
        detail.scrollTop = detailScrollTop;
        const chart = detail.querySelector?.(".trajectory-ptc-chart");
        const jsonPanel = detail.querySelector?.(".trajectory-ptc-json-panel");
        if (chart) chart.scrollTop = ptcChartScrollTop;
        if (jsonPanel) jsonPanel.scrollTop = ptcJsonScrollTop;
      }
    }
    list.scrollTop = listScrollTop;
    if (selectedId !== renderedSelectedId) onSelectEvent?.(selected);
    renderedSelectedId = selectedId; renderedDetailTab = detailTab;
    window.lucide?.createIcons();
  }

  function setHistory(nextHistory, { preserveView = false } = {}) {
    const sessionChanged = nextHistory?.session_id && history.session_id
      && nextHistory.session_id !== history.session_id;
    history = nextHistory || { items: [] };
    historyEvents = buildTrajectory(history);
    liveEvents = [];
    pendingResults = [];
    liveCalls = new WeakMap();
    runTurns.clear();
    for (const event of history.items || []) if (event.run_id && event.turn_id) runTurns.set(event.run_id, event.turn_id);
    currentRunId = null;
    activeAssistantLoop = null;
    if (!preserveView || sessionChanged || !historyEvents.some((event) => event.id === selectedId)) {
      selectedId = historyEvents.at(-1)?.id || null;
      selectionPinned = false;
      detailTab = "summary";
      ptcVisible = 20; ptcExpanded = null; ptcDetailTab = "result";
    }
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
    assistant.summary = assistantLoopSummary(assistant.payload, assistant.status);
    assistant.timestamp_end = timestamp;
    return assistant;
  }

  function resultTarget(frame) {
    const rows = [...historyEvents, ...liveEvents].filter(row => sameTool(row, frame.payload.tool_call_id, frame.payload.tool));
    // A result fact carries its actual Run and source event. Its paired call
    // can belong to the earlier UI Run, even when the tool is now completed.
    const facts = new Map((history.items || []).map(event => [event.event_id, event]));
    const durable = rows.filter(row => {
      const result = facts.get(row.native?.result_event_id);
      const part = storedMessage(result)?.parts?.[row.native?.result_part_offset || 0];
      return result?.run_id === frame.runId && part && sameToolValue(part.content, frame.payload.result)
        && (!frame.turnId || result.turn_id === frame.turnId);
    });
    if (durable.length) return { row: durable.length === 1 ? durable[0] : null, durable: true };
    const bound = rows.find(row => frame.callKey && callIdentity(row) === frame.callKey && !row.native?.result_event_id);
    if (bound) return { row: bound };
    return { row: pendingToolCall(rows.filter(row => (row.native?.call_event_id || liveCalls.has(row))
      && !row.native?.result_event_id && ["pending", "running"].includes(row.status)), frame.runId, frame.turnId || runTurns.get(frame.runId),
      row => Boolean(frame.payload.tool) && facts.get(row.native?.call_event_id)?.status === "pending") };
  }

  function reconcileResults() {
    pendingResults = pendingResults.filter(frame => {
      const { row, durable } = resultTarget(frame);
      if (durable) return false; // Persisted result owns content, timing and status.
      if (!row) return true; // Wait for actual turn_id/call evidence instead of making a second tool row.
      frame.callKey = callIdentity(row);
      const payload = frame.payload;
      row.result = redactTrajectoryValue(jsonClone({ result: payload.result, outcome: payload.outcome || null,
        artifacts: payload.artifacts || null, plan: payload.plan || null,
        metadata: { code_mode: payload.metadata?.code_mode, nested_calls: payload.metadata?.nested_calls, ptc_diagnostics: payload.metadata?.ptc_diagnostics, ...(row.label === "view_image" ? { view_image: payload.metadata?.view_image } : {}) } }));
      row.summary = liveSummary("tool_result", payload);
      row.status = toolResultPresentation(payload).status;
      if (row.duration_source !== "execution") row.timestamp_end = frame.timestamp;
      applyExecutionTiming(row, payload.timing, "tool_execution");
      return true;
    });
  }

  function replaceLiveCalls(stored) {
    const calls = historyEvents.filter(row => row.native?.call_event_id === stored.event_id);
    const candidates = new Map(liveEvents.filter(row => liveCalls.has(row)).map(row => [row, calls.filter(call => {
      const source = storedMessage(stored)?.parts?.[call.native.call_part_offset || 0];
      return row.run_id === call.run_id && (!row.turn_id || row.turn_id === call.turn_id)
        && Number(stored.sequence) > liveCalls.get(row).baseline
        && sameTool(row, call.tool_call_id, call.label) && sameToolArgs(liveCalls.get(row).args, source?.args);
    })]));
    for (const call of calls) {
      // Both directions must be unique. One live frame cannot arbitrarily
      // choose one of two identical calls inside a complete Message fact.
      const matches = [...candidates].filter(([, targets]) => targets.length === 1 && targets[0] === call).map(([row]) => row);
      if (matches.length !== 1) continue;
      const live = matches[0];
      for (const frame of pendingResults) if (frame.callKey === live.id) frame.callKey = callIdentity(call);
      if (selectedId === live.id) selectedId = call.id;
      liveEvents = liveEvents.filter(row => row !== live);
    }
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
      const newAssistantMessage = stored.event_type === "assistant/message"
        && !rawItems.some(item => item.event_type === "assistant/message"
          && assistantMessageKey(item) === assistantMessageKey(stored));
      const rawIndex = rawItems.findIndex((item) => item.event_id === stored.event_id);
      if (rawIndex >= 0) rawItems[rawIndex] = stored;
      else rawItems.push(stored);
      history.items = rawItems.sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
      historyEvents = buildTrajectory(history);
      if (stored.run_id && stored.turn_id) runTurns.set(stored.run_id, stored.turn_id);
      if (stored.event_type === "user/message") {
        if (liveInput) liveEvents = liveEvents.filter((item) => item !== liveInput);
      }
      if (stored.event_type === "output/attempt") liveEvents = liveEvents.filter(item => item.id !== stored.event_id);
      if (stored.event_type === "compaction") {
        liveEvents = liveEvents.filter((item) => !(item.kind === "compaction" && item.compaction_event_id === stored.event_id));
      }
      if (rawIndex < 0) replaceLiveCalls(stored);
      reconcileResults();
      if (newAssistantMessage && stored.run_id) {
        // A terminal frame may already have closed this Run's live loop. Its
        // first persisted fact still replaces that loop, including its receipt
        // timestamp. Later parts/updates belong to the same persisted message
        // and must not consume the next loop that is already streaming.
        const liveAssistant = liveEvents.find(item => item.kind === "assistant" && item.run_id === stored.run_id);
        if (liveAssistant) {
          liveEvents = liveEvents.filter(item => item !== liveAssistant);
          if (activeAssistantLoop === liveAssistant) activeAssistantLoop = null;
        }
      }
      const projectedMessage = historyEvents.find((item) => item.native?.event_ids?.includes(stored.event_id));
      const projectedTool = historyEvents.find((item) => TOOL_KINDS.has(item.kind)
        && [item.native?.call_event_id, item.native?.result_event_id].includes(stored.event_id));
      // Checkpoints keep arriving while a user inspects an earlier event.
      // Follow new facts only until the user explicitly chooses a row.
      if (!selectionPinned || !allEvents().some((item) => item.id === selectedId)) {
        selectedId = projectedTool?.id || projectedMessage?.id || stored.event_id;
      }
      scheduleRender();
      return;
    }
    if (type === "output") {
      if (!payload.output_id || historyEvents.some(item => item.id === payload.output_id)) return;
      const existing = liveEvents.find(item => item.id === payload.output_id);
      const projected = projectOutputAttempt({ event_id: payload.output_id, sequence: payload.sequence, turn_id: payload.turn_id, run_id: payload.run_id, event_type: "output/attempt", created_at: new Date().toISOString(), payload }, Math.max(trajectoryStats(allEvents()).inputs, 1), history.items || []);
      if (existing) Object.assign(existing, projected); else liveEvents.push(projected);
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
        label: "Compaction",
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
      if (currentRunId && payload.turn_id) runTurns.set(currentRunId, payload.turn_id);
      const currentUser = [...liveEvents].reverse().find((item) => item.kind === "input" && !item.run_id);
      if (currentUser && currentRunId) currentUser.run_id = currentRunId;
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
    if (type === "tool_call" && payload.tool_call_id) {
      const facts = new Map((history.items || []).map(item => [item.event_id, item]));
      const known = [...historyEvents, ...liveEvents].filter(row => (row.native?.call_event_id || liveCalls.has(row))
        && row.run_id === (payload.run_id || currentRunId) && sameTool(row, payload.tool_call_id, payload.tool)
        && sameToolArgs(liveCalls.has(row) ? liveCalls.get(row).args
          : storedMessage(facts.get(row.native.call_event_id))?.parts?.[row.native.call_part_offset || 0]?.args, payload.args));
      if (known.length) return; // Fact/replay frames already describe this call; never merge distinct fact rows.
    }
    if (type === "tool_result" && payload.tool_call_id) {
      const frame = { payload, runId: payload.run_id || currentRunId, turnId: payload.turn_id || runTurns.get(payload.run_id || currentRunId), timestamp };
      if (!pendingResults.some(existing => existing.runId === frame.runId && sameToolValue(existing.payload, payload))) {
        pendingResults.push(frame);
        pendingResults = pendingResults.slice(-256);
      }
      reconcileResults();
      if (activeAssistantLoop?.run_id === currentRunId) {
        activeAssistantLoop.status = "completed";
        activeAssistantLoop.summary = assistantLoopSummary(activeAssistantLoop.payload, activeAssistantLoop.status);
        activeAssistantLoop = null;
      }
      scheduleRender();
      return;
    }
    if (type === "tool_call_removed" && payload.tool_call_id) {
      const call = [...liveEvents].reverse().find(item => item.run_id === (payload.run_id || currentRunId)
        && sameTool(item, payload.tool_call_id, payload.tool));
      if (call) {
        call.status = "interrupted";
        call.summary = liveSummary(type, payload);
        call.timestamp_end = timestamp;
        if (activeAssistantLoop?.run_id === currentRunId) {
          activeAssistantLoop.status = "interrupted";
          activeAssistantLoop.summary = assistantLoopSummary(activeAssistantLoop.payload, activeAssistantLoop.status);
          activeAssistantLoop = null;
        }
        scheduleRender();
        return;
      }
    }
        if (type === "final") {
          liveEvents.filter((item) => item.run_id === currentRunId && item.status === "running")
            .forEach((item) => {
              item.status = "completed"; item.timestamp_end = timestamp;
              if (item.kind === "assistant") item.summary = assistantLoopSummary(item.payload, item.status);
            });
          activeAssistantLoop = null;
          scheduleRender();
          return;
    }
    if (["cancelled", "error"].includes(type)) {
      liveEvents.filter((item) => item.run_id === currentRunId && ["running", "pending"].includes(item.status))
        .forEach((item) => {
          item.status = type === "error" ? "failed" : "interrupted"; item.timestamp_end = timestamp;
          if (item.kind === "assistant") item.summary = assistantLoopSummary(item.payload, item.status);
        });
      activeAssistantLoop = null;
      // Transport termination closes live content; only a persisted execution
      // error is a separate trajectory fact. Cancellation adds no receipt.
      scheduleRender();
      return;
    }

    const kind = liveEventKind(type, payload);
    const status = type === "tool_result" ? toolResultPresentation(payload).status
      : type === "tool_call" ? "pending" : "running";
    const live = {
      id: `live-${type}-${payload.tool_call_id || Date.now()}-${liveEvents.length}`,
      kind,
      input_sequence: inputSequence,
      run_id: payload.run_id || currentRunId,
      turn_id: payload.turn_id || runTurns.get(payload.run_id || currentRunId) || null,
      timestamp,
      status,
      label: liveLabel(type, payload),
      summary: liveSummary(type, payload),
      tool_call_id: payload.tool_call_id || null,
      source: payload.source || "sse",
      payload: redactTrajectoryValue(jsonClone(type === "tool_result" ? null : payload)),
      result: redactTrajectoryValue(jsonClone(type === "tool_result" ? payload : null)),
      native: null,
    };
    liveEvents.push(live);
    if (type === "tool_call") {
      liveCalls.set(live, { args: jsonClone(payload.args),
        baseline: (history.items || []).reduce((max, item) => Math.max(max, Number(item.sequence) || 0), 0) });
      reconcileResults();
    }
    scheduleRender();
  }

  function exportTrace() {
    if (!exportReady || disposed) return;
    const payload = {
      format: "deepagent-session-trajectory",
      version: 1,
      source: readOnly ? "history" : "history+sse",
      generated_at: new Date().toISOString(),
      session_id: state.sessionId || history.session_id || null,
      conversation_id: state.conversationId || history.conversation_id || null,
      limitations: [
        "不包含系统提示词、动态工具Schema、认证Header、Token或Cookie",
        "历史轨迹没有持久化逐Part精确耗时",
        "同一模型响应的 Part 合并展示；native.event_ids 保留对应数据库事件 ID",
      ],
      events: redactTrajectoryValue(allEvents()),
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

  listen(byId("chatViewTabs"), "click", (event) => {
    const button = event.target.closest("[data-chat-view]");
    if (button) selectView(button.dataset.chatView);
  });
  listen(query(".trajectory-modes"), "click", (event) => {
    const button = event.target.closest("[data-trajectory-mode]");
    if (!button) return;
    mode = button.dataset.trajectoryMode;
    queryAll("[data-trajectory-mode]").forEach((item) => item.classList.toggle("active", item === button));
    scheduleRender();
  });
  listen(search, "input", scheduleRender);
  listen(clockToggle, "click", () => {
    clockMode = clockMode === "time" ? "duration" : "time";
    scheduleRender();
  });
  listen(exportButton, "click", exportTrace);
  listen(list, "click", (event) => {
        const row = event.target.closest("[data-trajectory-id]");
    if (!row) return;
    selectedId = row.dataset.trajectoryId;
    selectionPinned = true;
    detailTab = "summary";
    ptcVisible = 20; ptcExpanded = null; ptcDetailTab = "result";
    scheduleRender();
  });
  listen(detail, "click", (event) => {
    const ptcStep = event.target.closest("[data-ptc-step]");
    if (ptcStep) {
      const next = Number(ptcStep.dataset.ptcStep);
      ptcExpanded = next;
      ptcDetailTab = "result";
      scheduleRender();
      return;
    }
    const ptcTab = event.target.closest("[data-ptc-detail-tab]");
    if (ptcTab) {
      ptcDetailTab = ptcTab.dataset.ptcDetailTab;
      scheduleRender();
      return;
    }
    if (event.target.closest("[data-ptc-load-more]")) {
      ptcVisible += 20;
      scheduleRender();
      return;
    }
    const copyFile = event.target.closest("[data-copy-file-id]");
    if (copyFile) {
      copyTextToClipboard(copyFile.dataset.copyFileId || "").then(() => {
        copyFile.classList.add("copied");
        copyFile.textContent = "Copied";
      });
      return;
    }
    const copyCode = event.target.closest("[data-copy-run-code]");
    if (copyCode) {
      const selected = allEvents().find((item) => item.id === selectedId);
      copyTextToClipboard(runCodeSource(selected)).then(() => {
        copyCode.classList.add("copied");
        copyCode.textContent = "Copied";
      });
      return;
    }
    const eventTarget = event.target.closest("[data-trajectory-event-target]");
    if (eventTarget) {
      selectedId = eventTarget.dataset.trajectoryEventTarget;
      selectionPinned = true;
      detailTab = "summary";
      ptcVisible = 20; ptcExpanded = null; ptcDetailTab = "result";
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
  function focus({ eventId = null, runId = null } = {}) {
    const target = allEvents().find((event) => eventId
      ? event.id === eventId || event.native?.event_id === eventId || event.native?.event_ids?.includes(eventId)
      : runId && event.run_id === runId);
    if (!target) return false;
    selectedId = target.id; detailTab = "summary"; ptcVisible = 20; ptcExpanded = null; ptcDetailTab = "result";
    selectionPinned = true;
    mode = "sequence"; if (search) search.value = "";
    queryAll("[data-trajectory-mode]").forEach((button) => button.classList.toggle("active", button.dataset.trajectoryMode === mode));
    selectView("trajectory", { notify: false }); render();
    centerInScrollContainer(list, [...list.querySelectorAll("[data-trajectory-id]")].find((row) => row.dataset.trajectoryId === target.id));
    return true;
  }
  function snapshot() {
    return { selectedId, selectionPinned, detailTab, ptcVisible, ptcExpanded, ptcDetailTab, mode, clockMode, view: activeView, query: search?.value || "", listScrollTop: list.scrollTop, detailScrollTop: detail.scrollTop };
  }
  function restore(value = {}) {
    clockMode = value.clockMode === "duration" ? "duration" : "time";
    if (value.selectedId && allEvents().some((event) => event.id === value.selectedId)) {
      selectedId = value.selectedId;
      selectionPinned = value.selectionPinned !== false;
    }
    detailTab = value.detailTab || "summary"; mode = value.mode === "calls" ? "calls" : "sequence";
    ptcVisible = Math.max(20, Number(value.ptcVisible) || 20);
    ptcExpanded = Number.isInteger(value.ptcExpanded) ? value.ptcExpanded : null;
    ptcDetailTab = ["input", "result", "error", "code"].includes(value.ptcDetailTab) ? value.ptcDetailTab : "result";
    if (search) search.value = value.query || "";
    queryAll("[data-trajectory-mode]").forEach((button) => button.classList.toggle("active", button.dataset.trajectoryMode === mode));
    selectView(value.view, { notify: false }); render();
    list.scrollTop = Number(value.listScrollTop) || 0; detail.scrollTop = Number(value.detailScrollTop) || 0;
  }
  function destroy() {
    disposed = true; if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    bindings.splice(0).forEach((remove) => remove()); historyEvents = []; liveEvents = []; history = {};
    pendingResults = []; runTurns.clear(); liveCalls = new WeakMap();
  }
  return { setHistory, setExportReady, reset, addUser, ingest, selectView, focus, snapshot, restore, destroy, events: allEvents };
}
