function checkpointBoundary(checkpoint) {
  const raw = checkpoint?.source_last_sequence ?? checkpoint;
  if (raw == null || raw === "") return null;
  const value = Number(raw);
  return Number.isInteger(value) ? value : null;
}

function crossesCheckpoint(message, boundary) {
  return (
    boundary != null
    && Number.isInteger(message?.branch_point)
    && message.branch_point > boundary
  );
}

/**
 * Convert persisted messages into visual rows. Consecutive assistant records
 * belong to one answer until a user turn or the active Checkpoint boundary.
 */
export function buildHistoryVisualItems(messages = [], checkpoint = null) {
  const items = [];
  const boundary = checkpointBoundary(checkpoint);
  let checkpointInserted = boundary == null;
  let assistantRecords = [];

  const flushAssistant = () => {
    if (!assistantRecords.length) return;
    items.push({ type: "assistant", records: assistantRecords });
    assistantRecords = [];
  };

  for (const message of messages) {
    if (!checkpointInserted && crossesCheckpoint(message, boundary)) {
      flushAssistant();
      items.push({ type: "checkpoint" });
      checkpointInserted = true;
    }

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
  if (!checkpointInserted) items.push({ type: "checkpoint" });
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

export const PLANNING_TOOL_NAMES = new Set([
  "write_plan",
  "read_plan",
  "add_task",
  "update_task_status",
  "update_task_statuses",
  "remove_task",
]);

export function isPlanningToolEvent(event = {}) {
  return event.source === "planning" || PLANNING_TOOL_NAMES.has(event.tool);
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
  if (terminalStatus === "completed" || terminalStatus === "pending") return null;
  const allCompleted = plan.completed === plan.total;
  if (allCompleted) return null;
  const terminalLabels = {
    interrupted: "已中断",
    error: "执行失败",
  };
  const terminalLabel = terminalLabels[terminalStatus] || "";
  return {
    plan,
    label: `${plan.completed}/${plan.total}${terminalLabel ? ` · ${terminalLabel}` : ""}`,
    terminalStatus,
  };
}

/** Return the final Planning snapshot from the latest assistant run only. */
export function latestAssistantRunPlan(messages = []) {
  let latestIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "assistant") {
      latestIndex = index;
      break;
    }
    if (messages[index]?.role === "user") return null;
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
    for (let index = latestIndex; index >= 0; index -= 1) {
      const message = messages[index];
      if (message?.role !== "assistant" || message.run_id) break;
      records.unshift(message);
    }
  }

  let snapshot = null;
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
