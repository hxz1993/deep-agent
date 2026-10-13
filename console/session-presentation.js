import { buildTrajectory, redactTrajectoryValue } from "./trajectory.js?v=20261013-session-navigation";
import { cacheSummary } from "./cache-usage.js?v=20260923-usage-tab-2";
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Saved terminal facts only; an active transport is the host's separate override. */
export function savedRunState(events = []) {
  const executionTypes = new Set(["user/message", "assistant/message", "tool/results", "model/request", "execution/error"]);
  const executions = events.filter(event => executionTypes.has(event.event_type))
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
  if (!executions.length) return { run_id: null, status: "not_started" };
  // Match diagnostics.latest_status_expression: a resumed Run can start with
  // tool results, and must never inherit the preceding Run's terminal state.
  const runId = executions.at(-1).run_id ?? null;
  const terminal = executions.findLast(event => (event.run_id ?? null) === runId
    && ["user/message", "assistant/message", "execution/error"].includes(event.event_type));
  let status = "unknown";
  if (terminal?.event_type === "execution/error") status = "error";
  else if (terminal?.event_type === "assistant/message") {
    status = { pending: "waiting", completed: "completed", interrupted: "stopped", cancelled: "stopped", failed: "error" }[terminal.status] || "unknown";
  }
  return { run_id: runId, status };
}

/** Shared trajectory grouping contains complete message usage, not one count per part. */
export function savedRunFacts(events = [], runId = null) {
  const messages = buildTrajectory({ items: events }).filter((item) => item.kind === "assistant" && (!runId || item.run_id === runId));
  const usage = {};
  let missingResponses = 0;
  for (const item of messages) {
    const reported = item.native?.usage || {};
    const hasTokens = ["input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens"].some(key => Number(reported[key]) > 0);
    if (!hasTokens) missingResponses += 1;
    for (const key of ["input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens", "requests", "tool_calls"]) {
      if (!hasTokens && key.endsWith("_tokens")) continue;
      const value = reported[key];
      if (key.startsWith("cache_") && Number(value) === 0) continue;
      if (value != null && Number.isFinite(Number(value))) usage[key] = (usage[key] || 0) + Number(value);
    }
  }
  if (usage.total_tokens == null && (usage.input_tokens != null || usage.output_tokens != null)) {
    usage.total_tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  }
  const latest = messages.at(-1)?.native || {};
  const cache_usage = cacheSummary(messages.map(item => item.native));
  for (const field of ["cache_read_tokens", "cache_write_tokens"]) {
    if (cache_usage[field] != null) usage[field] = cache_usage[field];
    else delete usage[field];
  }
  return { usage, cache_usage, usage_partial: missingResponses > 0 && missingResponses < messages.length, usage_missing_responses: missingResponses, conversation_id: latest.conversation_id || null, provider_name: latest.provider_name || null, model_name: latest.model_name || null, model_id: latest.model_id || null, finish_reason: latest.finish_reason || null };
}

/** Count SDK messages, not their separately persisted parts. */
export function savedSessionCounts(events = []) {
  const messages = events.filter(event => ["user/message", "assistant/message", "tool/results", "model/request"].includes(event.event_type));
  const keys = messages.map(event => Object.hasOwn(event.payload?.source || {}, "message_ordinal")
    ? JSON.stringify(["parts", event.run_id, event.payload.source.message_ordinal])
    : JSON.stringify(["message", event.event_id]));
  return {
    messageCount: new Set(keys).size,
    turnCount: new Set(messages.filter(event => event.event_type === "user/message").map(event => event.turn_id).filter(id => id != null)).size,
  };
}

export function renderContextReferenceContent(reference) {
  if (!reference) return `<div class="inspector-tab-empty"><i data-lucide="braces" aria-hidden="true"></i><strong>尚未选择上下文引用</strong><span>点击对话中引用旁的信息图标查看已保存内容。</span></div>`;
  const safe = redactTrajectoryValue(reference);
  const json = globalThis.DeepAgentJson?.renderBlock(safe, { className: "inspector-json", ariaLabel: "上下文引用 JSON" })
    || `<pre class="inspector-json" aria-label="上下文引用 JSON">${escapeHtml(JSON.stringify(safe, null, 2))}</pre>`;
  return `<section class="inspector-section context-reference-inspector"><header><strong>上下文引用</strong></header>
    <div class="inspector-facts compact">${[["名称", safe.label], ["Key", safe.key], ["类型", safe.type], ["版本", safe.schema_version]].map(([label, value]) => `<div><span>${label}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`).join("")}</div>${json}</section>`;
}

export const sessionStatusLabels = { running: "进行中", pending: "待操作", waiting: "待操作", awaiting_input: "待操作", completed: "已完成", stopped: "已停止", cancelled: "已停止", interrupted: "已停止", error: "异常", failed: "异常", unknown: "未确认", unconfirmed: "未确认", not_started: "未开始", empty: "未开始" };

/** Metadata is authoritative and available before the lazy trace has loaded. */
export function savedRunPresentation(session = {}, events = [], selectedRunId = null) {
  const runId = selectedRunId || session.run_id || session.runs?.at(-1)?.run_id;
  const storedRun = session.runs?.find((item) => item.run_id === runId) || {};
  const facts = events.length ? savedRunFacts(events, runId) : {};
  return {
    run: { ...facts, ...storedRun, run_id: runId, status: storedRun.status || session.latest_status },
    usage: storedRun.usage || facts.usage || {},
    model: session.model_name ? { name: session.model_name } : null,
    // The last model of the whole session need not be the model of this run.
    runModel: storedRun.model_name || facts.model_name ? { name: storedRun.model_name || facts.model_name } : null,
  };
}
