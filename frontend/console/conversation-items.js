function itemOrder(item) {
  return [Number(item?.order?.sequence || 0), Number(item?.order?.part_offset || 0), String(item?.item_id || "")];
}

export function sortConversationItems(items = []) {
  return [...items].sort((left, right) => {
    const a = itemOrder(left);
    const b = itemOrder(right);
    return a[0] - b[0] || a[1] - b[1] || a[2].localeCompare(b[2]);
  });
}

export function normalizeConversationHistory(response = {}) {
  return {
    schemaVersion: Number(response.schema_version || 1),
    sessionId: String(response.session_id || ""),
    eventHeadSequence: Number(response.event_head_sequence || 0),
    items: sortConversationItems(Array.isArray(response.items) ? response.items : []),
  };
}

export function upsertConversationItem(items = [], item) {
  if (!item?.item_id) return sortConversationItems(items);
  const next = items.filter((candidate) => candidate?.item_id !== item.item_id);
  next.push(item);
  return sortConversationItems(next);
}

export function conversationVisualGroups(items = []) {
  const groups = [];
  let assistantItems = [];
  const flushAssistant = () => {
    if (!assistantItems.length) return;
    groups.push({ type: "assistant", items: assistantItems });
    assistantItems = [];
  };
  for (const item of sortConversationItems(items)) {
    if (item.kind === "user_message") {
      flushAssistant();
      groups.push({ type: "user", item });
    } else {
      assistantItems.push(item);
    }
  }
  flushAssistant();
  return groups;
}

export function latestPendingDeferredTool(items = []) {
  const ordered = sortConversationItems(items);
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const item = ordered[index];
    if (item.kind === "user_message") break;
    if (item.kind === "tool" && item.status === "awaiting_input") return item;
  }
  return null;
}
