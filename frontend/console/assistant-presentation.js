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
