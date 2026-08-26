/**
 * Convert projected Event messages into visual rows. Compaction placement is
 * handled by session-events.js from the original Event sequence.
 */
export function buildHistoryVisualItems(messages = []) {
  const items = [];
  let assistantRecords = [];

  const flushAssistant = () => {
    if (!assistantRecords.length) return;
    items.push({ type: "assistant", records: assistantRecords });
    assistantRecords = [];
  };

  for (const message of messages) {
    if (message?.role === "assistant") {
      assistantRecords.push(message);
      continue;
    }

    flushAssistant();
    if (message?.role === "user") {
      items.push({ type: "user", message });
    }
  }

  flushAssistant();
  return items;
}

function isRenderablePart(part) {
  if (!part || typeof part !== "object") return false;
  if (["thinking", "text"].includes(part.type)) return Boolean(part.content);
  return ["tool_call", "tool_result"].includes(part.type);
}

/**
 * The last renderable text part is final only when nothing process-like follows
 * it. Earlier text, thinking and tool lifecycle parts are process material.
 */
export function splitAssistantHistoryRecords(records = []) {
  const parts = records.flatMap((record) => Array.isArray(record?.parts) ? record.parts : []);
  let lastRenderableIndex = -1;
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (isRenderablePart(parts[index])) {
      lastRenderableIndex = index;
      break;
    }
  }

  const finalIndex = lastRenderableIndex >= 0 && parts[lastRenderableIndex]?.type === "text"
    ? lastRenderableIndex
    : -1;

  return {
    processParts: parts.filter((part, index) => isRenderablePart(part) && index !== finalIndex),
    finalText: finalIndex >= 0 ? String(parts[finalIndex].content || "") : "",
    interrupted: records.some((record) => record?.status === "interrupted"),
    statuses: records.map((record) => record?.status).filter(Boolean),
  };
}

export function processDisclosureLabel({ pending = false, running = false } = {}) {
  if (pending) return "处理过程 · 等待操作";
  if (running) return "处理过程 · 处理中";
  return "处理过程";
}

export const PLANNING_TOOL_NAMES = new Set(["write_plan"]);

export function isPlanningToolEvent(event = {}) {
  return PLANNING_TOOL_NAMES.has(event.tool);
}

export function normalizePlanSnapshot(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.items)) return null;
  const items = snapshot.items
    .filter((item) => item && typeof item === "object")
    .map((item, index) => ({
      id: String(item.id || `plan-item-${index + 1}`),
      content: String(item.content || "未命名任务"),
      active_form: item.active_form ? String(item.active_form) : null,
      status: String(item.status || "pending"),
    }));
  if (!items.length) return null;
  const completed = items.filter((item) => item.status === "completed").length;
  return { items, total: items.length, completed };
}

export function planPanelState(snapshot, terminalStatus = null) {
  const plan = normalizePlanSnapshot(snapshot);
  if (!plan) return null;
  const allCompleted = plan.completed === plan.total;
  const presentationStatus = terminalStatus || (allCompleted ? "completed" : null);
  const terminalLabels = {
    interrupted: "已中断",
    error: "执行失败",
    pending: "等待交互",
    completed: allCompleted ? "已完成" : "回合结束",
  };
  const terminalLabel = terminalLabels[presentationStatus] || "";
  return {
    plan,
    label: `${plan.completed}/${plan.total}${terminalLabel ? ` · ${terminalLabel}` : ""}`,
    terminalStatus: presentationStatus,
  };
}

/** Return the newest persisted Planning snapshot for session-level display. */
export function latestAssistantRunPlan(messages = []) {
  let latestIndex = -1;
  let snapshot = null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") continue;
    const parts = Array.isArray(message.parts) ? message.parts : [];
    for (let partIndex = parts.length - 1; partIndex >= 0; partIndex -= 1) {
      const part = parts[partIndex];
      if (part?.type === "tool_result" && isPlanningToolEvent(part) && part.plan) {
        latestIndex = index;
        snapshot = part.plan;
        break;
      }
    }
    if (snapshot) break;
  }
  if (latestIndex < 0) return null;

  const latest = messages[latestIndex];
  const runId = latest.run_id || null;
  const records = [];
  if (runId) {
    for (const message of messages) {
      if (message?.role === "assistant" && message.run_id === runId) records.push(message);
    }
  } else {
    let start = latestIndex;
    while (start > 0 && messages[start - 1]?.role !== "user") start -= 1;
    let end = latestIndex;
    while (end + 1 < messages.length && messages[end + 1]?.role !== "user") end += 1;
    for (let index = start; index <= end; index += 1) {
      const message = messages[index];
      if (message?.role === "assistant" && !message.run_id) records.push(message);
    }
  }

  const pendingUiCalls = new Set();
  for (const record of records) {
    for (const part of record.parts || []) {
      if (
        part?.type === "tool_result"
        && isPlanningToolEvent(part)
        && part.plan
      ) {
        snapshot = part.plan;
      }
      if (part?.type === "tool_call" && part.tool_call_id && part.widget) {
        pendingUiCalls.add(part.tool_call_id);
      } else if (part?.type === "tool_result" && part.tool_call_id) {
        pendingUiCalls.delete(part.tool_call_id);
      }
    }
  }
  if (!snapshot) return null;

  const statuses = records.map((record) => record?.status).filter(Boolean);
  let terminalStatus = null;
  if (statuses.includes("interrupted")) terminalStatus = "interrupted";
  else if (statuses.includes("error") || statuses.includes("failed")) terminalStatus = "error";
  else if (statuses.includes("pending") || pendingUiCalls.size) terminalStatus = "pending";
  else if (statuses.length && statuses.every((status) => status === "completed")) {
    terminalStatus = "completed";
  }
  return { snapshot, runId, terminalStatus };
}
