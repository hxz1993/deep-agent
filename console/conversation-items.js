import { assistantActionsFromHistoryItems } from "./assistant-presentation.js?v=20260907-1";

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

export function projectConversationItems(items = []) {
  const nodes = [];
  let assistantItems = [];
  let turnStartedAt = null;
  const flushAssistant = () => {
    if (!assistantItems.length) return;
    const first = assistantItems[0];
    const finalAssistant = [...assistantItems]
      .reverse()
      .find((item) => item?.kind === "assistant_message" && item?.phase === "final");
    const last = assistantItems[assistantItems.length - 1];
    nodes.push({
      type: "assistant",
      itemId: first?.item_id || "",
      createdAt: first?.created_at || null,
      startedAt: turnStartedAt || first?.created_at || null,
      completedAt: finalAssistant?.updated_at || last?.updated_at || last?.created_at || null,
      order: first?.order || null,
      actions: assistantActionsFromHistoryItems(assistantItems),
    });
    assistantItems = [];
  };
  for (const item of sortConversationItems(items)) {
    if (item.kind === "user_message") {
      flushAssistant();
      turnStartedAt = item.created_at || null;
      nodes.push({
        type: "user",
        itemId: item.item_id || "",
        createdAt: item.created_at || null,
        order: item.order || null,
        text: item.text || "",
        attachments: Array.isArray(item.attachments) ? item.attachments : [],
        contextRefs: Array.isArray(item.context_refs) ? item.context_refs : [],
      });
    } else if (["assistant_message", "tool"].includes(item.kind)) {
      assistantItems.push(item);
    }
  }
  flushAssistant();
  return nodes;
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
