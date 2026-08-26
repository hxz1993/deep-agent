function eventMessage(event) {
  return event?.payload?.message && typeof event.payload.message === "object"
    ? event.payload.message
    : null;
}

function splitUserContent(content) {
  if (typeof content === "string") {
    return { content: content || null, attachments: [], context_refs: [] };
  }
  if (!Array.isArray(content)) {
    return { content: null, attachments: [], context_refs: [] };
  }
  const texts = [];
  const attachments = [];
  const contextRefs = [];
  for (const item of content) {
    if (typeof item === "string") texts.push(item);
    else if (item?.kind === "text-content") texts.push(String(item.content || ""));
    else if (item?.kind === "attachment-ref") attachments.push({
      id: item.id,
      name: item.name || null,
      mime: item.mime || item.mime_type || null,
      byte_size: item.byte_size ?? null,
      content_hash: item.content_hash || null,
    });
    else if (item?.kind === "context-ref") {
      const { kind: _kind, ...reference } = item;
      contextRefs.push(reference);
    }
  }
  return {
    content: texts.filter(Boolean).join("\n") || null,
    attachments,
    context_refs: contextRefs,
  };
}

function toolArtifacts(metadata = {}) {
  const artifacts = [];
  if (metadata.overflow_handle) artifacts.push({
    artifact_id: metadata.overflow_handle,
    kind: "tool_result_overflow",
    role: "return_value",
    byte_size: metadata.overflow_bytes ?? null,
    content_hash: metadata.overflow_hash || null,
  });
  if (metadata.overflow_content_handle) artifacts.push({
    artifact_id: metadata.overflow_content_handle,
    kind: "tool_result_overflow",
    role: "content",
    content_hash: metadata.overflow_content_hash || null,
  });
  return artifacts;
}

function sourceForTool(toolName) {
  if (["search_tools", "tool_search"].includes(toolName)) return "tool_search";
  if (toolName === "write_plan") return "planning";
  return null;
}

function historyPart(part) {
  const kind = part?.part_kind;
  if (kind === "text") return { type: "text", content: part.content || "" };
  if (kind === "thinking") return { type: "thinking", content: part.content || "" };
  if (["tool-call", "builtin-tool-call"].includes(kind)) {
    const tool = part.tool_name || null;
    return {
      type: "tool_call",
      tool,
      args: part.args ?? null,
      tool_call_id: part.tool_call_id || null,
      source: sourceForTool(tool),
      widget: part.metadata?.widget || null,
      metadata: part.metadata || null,
    };
  }
  if (["tool-return", "builtin-tool-return", "retry-prompt"].includes(kind)) {
    const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
    const tool = part.tool_name || null;
    return {
      type: "tool_result",
      tool,
      result: part.content ?? null,
      tool_call_id: part.tool_call_id || null,
      source: sourceForTool(tool),
      artifacts: toolArtifacts(metadata),
      plan: tool === "write_plan" ? metadata.deepagent_plan || null : null,
      outcome: part.outcome || (kind === "retry-prompt" ? "retry" : null),
      timestamp: part.timestamp || null,
      metadata,
    };
  }
  if (kind === "file") return { type: "file", ...part };
  if (kind === "speech") return { type: "speech", ...part };
  return null;
}

function userMessage(event, message) {
  const prompt = (message.parts || []).find((part) => part?.part_kind === "user-prompt");
  if (!prompt) return null;
  return {
    role: "user",
    ...splitUserContent(prompt.content),
    parts: [],
    instructions: message.instructions || null,
    system_parts: (message.parts || []).filter((part) => ["system-prompt", "instruction"].includes(part?.part_kind)),
    timestamp: prompt.timestamp || message.timestamp || event.created_at || null,
    event_id: event.event_id,
    sequence: event.sequence,
    turn_id: event.turn_id || null,
    run_id: event.run_id || message.run_id || null,
    raw_message: message,
  };
}

function assistantRecord(event, message) {
  const parts = (message.parts || []).map(historyPart).filter(Boolean);
  if (!parts.length) return null;
  return {
    role: "assistant",
    parts,
    timestamp: message.timestamp || event.created_at || null,
    request_timestamp: null,
    event_id: event.event_id,
    sequence: event.sequence,
    turn_id: event.turn_id || null,
    run_id: event.run_id || message.run_id || null,
    status: message.state === "interrupted" ? "interrupted" : event.status || "completed",
    model_name: message.model_name || null,
    provider_name: message.provider_name || null,
    provider_response_id: message.provider_response_id || null,
    finish_reason: message.finish_reason || null,
    usage: message.usage || null,
    raw_message: message,
  };
}

/** Deterministic projection from complete Message Events. */
export function projectSessionEvents(response = {}) {
  const events = [...(Array.isArray(response?.items) ? response.items : [])]
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
  const messages = [];
  const messageByEventId = new Map();
  for (const event of events) {
    const message = eventMessage(event);
    if (!message) continue;
    const projected = event.event_type === "user/message"
      ? userMessage(event, message)
      : ["assistant/message", "tool/results"].includes(event.event_type)
        ? assistantRecord(event, message)
        : null;
    if (!projected) continue;
    messages.push(projected);
    messageByEventId.set(event.event_id, projected);
  }

  const visualItems = [];
  for (const event of events) {
    if (event.event_type === "compaction") {
      visualItems.push({ type: "compaction", event });
      continue;
    }
    const message = messageByEventId.get(event.event_id);
    if (!message) continue;
    if (message.role === "user") {
      visualItems.push({ type: "user", message });
      continue;
    }
    const previous = visualItems.at(-1);
    if (previous?.type === "assistant") previous.records.push(message);
    else visualItems.push({ type: "assistant", records: [message] });
  }

  return {
    events,
    messages,
    visualItems,
    messageCount: messages.length,
  };
}

export function compactionPresentation(event) {
  const budget = event?.payload?.budget || {};
  const source = event?.payload?.source || {};
  const summaryCall = event?.payload?.summary_call || {};
  return {
    event_id: event?.event_id || null,
    status: event?.status || null,
    before_tokens: budget.estimated_before ?? null,
    after_tokens: budget.estimated_after ?? null,
    through_sequence: source.through_sequence ?? null,
    updated_at: event?.updated_at || null,
    summary_model_id: summaryCall.model_id || null,
  };
}
