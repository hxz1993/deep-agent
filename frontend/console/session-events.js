function eventPart(event) {
  return event?.payload?.part && typeof event.payload.part === "object"
    ? event.payload.part
    : null;
}

function messageMeta(event) {
  return event?.payload?.message_meta && typeof event.payload.message_meta === "object"
    ? event.payload.message_meta
    : {};
}

function normalizedRunStatus(status) {
  if (["cancelled", "interrupted"].includes(status)) return "interrupted";
  if (["failed", "error"].includes(status)) return "error";
  if (status === "pending") return "pending";
  return status === "completed" ? "completed" : null;
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
    else if (item?.kind === "attachment-ref") {
      attachments.push({
        id: item.id,
        name: item.name || null,
        mime: item.mime || null,
        extracted_text: item.extracted_text || null,
      });
    } else if (item?.kind === "context-ref") {
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
  if (metadata.overflow_handle) {
    artifacts.push({
      artifact_id: metadata.overflow_handle,
      kind: "tool_result_overflow",
      role: "return_value",
      byte_size: metadata.overflow_bytes ?? null,
      content_hash: metadata.overflow_hash || null,
    });
  }
  if (metadata.overflow_content_handle) {
    artifacts.push({
      artifact_id: metadata.overflow_content_handle,
      kind: "tool_result_overflow",
      role: "content",
      content_hash: metadata.overflow_content_hash || null,
    });
  }
  return artifacts;
}

function sourceForTool(toolName) {
  if (["search_tools", "tool_search"].includes(toolName)) return "tool_search";
  if (toolName === "write_plan") return "planning";
  return null;
}

function eventPartForHistory(event) {
  const part = eventPart(event) || {};
  if (event.event_type === "assistant/text") {
    return { type: "text", content: part.content || "" };
  }
  if (event.event_type === "assistant/reasoning") {
    return { type: "thinking", content: part.content || "" };
  }
  if (["tool/call", "tool/search_call", "capability/load_call", "tool/native_call"].includes(event.event_type)) {
    return {
      type: "tool_call",
      tool: event.tool_name || part.tool_name || null,
      args: part.args ?? null,
      tool_call_id: event.call_id || part.tool_call_id || null,
      source: sourceForTool(event.tool_name || part.tool_name),
      metadata: part.metadata || null,
    };
  }
  if (["tool/result", "tool/search_result", "capability/load_result", "tool/native_result"].includes(event.event_type)) {
    const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
    return {
      type: "tool_result",
      tool: event.tool_name || part.tool_name || null,
      result: part.content ?? null,
      tool_call_id: event.call_id || part.tool_call_id || null,
      source: sourceForTool(event.tool_name || part.tool_name),
      artifacts: toolArtifacts(metadata),
      plan: event.tool_name === "write_plan" ? metadata.deepagent_plan || null : null,
      outcome: part.outcome || null,
      timestamp: part.timestamp || null,
    };
  }
  return null;
}

/** Deterministic Console projection from the public Session Event contract. */
export function projectSessionEvents(response = {}) {
  const events = [...(Array.isArray(response?.items) ? response.items : [])]
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
  const runStatuses = new Map(
    events
      .filter((event) => event.event_type === "run" && event.run_id)
      .map((event) => [event.run_id, normalizedRunStatus(event.status)]),
  );
  const groups = new Map();
  for (const event of events) {
    if (!event.message_id) continue;
    if (!groups.has(event.message_id)) groups.set(event.message_id, []);
    groups.get(event.message_id).push(event);
  }
  const messages = [];
  for (const group of groups.values()) {
    const first = group[0];
    const meta = messageMeta(first);
    const userEvent = group.find((event) => event.event_type === "user/message");
    if (userEvent) {
      const user = splitUserContent(eventPart(userEvent)?.content);
      messages.push({
        role: "user",
        ...user,
        parts: [],
        timestamp: eventPart(userEvent)?.timestamp || meta.timestamp || first.created_at || null,
        event_id: userEvent.event_id,
        sequence: first.sequence,
        turn_id: first.turn_id || null,
        run_id: first.run_id || meta.run_id || null,
      });
      continue;
    }
    const parts = group.map(eventPartForHistory).filter(Boolean);
    if (!parts.length) continue;
    const runId = first.run_id || meta.run_id || null;
    messages.push({
      role: "assistant",
      parts,
      timestamp: meta.timestamp || first.created_at || null,
      request_timestamp: null,
      event_id: first.event_id,
      sequence: first.sequence,
      turn_id: first.turn_id || null,
      run_id: runId,
      status: meta.state === "interrupted"
        ? "interrupted"
        : runStatuses.get(runId) || "completed",
      model_name: meta.model_name || null,
      provider_name: meta.provider_name || null,
      provider_response_id: meta.provider_response_id || null,
      finish_reason: meta.finish_reason || null,
      usage: meta.usage || null,
    });
  }

  const visualItems = [];
  const messageBySequence = new Map(messages.map((message) => [message.sequence, message]));
  for (const event of events) {
    if (event.event_type === "compaction") {
      visualItems.push({ type: "compaction", event });
      continue;
    }
    const message = messageBySequence.get(event.sequence);
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
    messageCount: new Set(events.map((event) => event.message_id).filter(Boolean)).size,
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
